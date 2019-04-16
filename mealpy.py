import getpass
import json
import time
from os import path, environ
from base64 import b64decode
import boto3
import requests
import strictyaml

BASE_DOMAIN = 'secure.mealpal.com'
BASE_URL = f'https://{BASE_DOMAIN}'
LOGIN_URL = f'{BASE_URL}/1/login'
CITIES_URL = f'{BASE_URL}/1/functions/getCitiesWithNeighborhoods'
MENU_URL = f'{BASE_URL}/api/v1/cities/{{}}/product_offerings/lunch/menu'
RESERVATION_URL = f'{BASE_URL}/api/v2/reservations'
KITCHEN_URL = f'{BASE_URL}/1/functions/checkKitchen3'

LOGGED_IN_COOKIE = 'isLoggedIn'

HEADERS = {
    'Host': BASE_DOMAIN,
    'Origin': BASE_URL,
    'Referer': f'{BASE_URL}/login',
    'Content-Type': 'application/json',
}

KEYRING_SERVICENAME = BASE_DOMAIN

# TODO(GH-4): Replace this feature toggle with cli option
USE_KEYRING = True


def load_config():
    schema = strictyaml.Map({
        'email_address': strictyaml.Email(),
        'meals': strictyaml.Seq(strictyaml.MapPattern(strictyaml.Str(), strictyaml.Str()))
    })

    root_dir = path.abspath(path.dirname(__file__))
    with open(path.join(root_dir, 'config.yaml')) as config_file:
        return strictyaml.load(config_file.read(), schema).data


class MealPal():

    def __init__(self, user, password=None):
        self.cookies = None
        self.user = user
        self.password = password

    def login(self):
        data = {
            'username': self.user,
            'password': self.password or keyring.get_password(KEYRING_SERVICENAME, self.user),
        }

        request = requests.post(LOGIN_URL, data=json.dumps(data), headers=HEADERS)

        self.cookies = request.cookies
        self.cookies.set(LOGGED_IN_COOKIE, 'true', domain=BASE_URL)

        return request.status_code

    @staticmethod
    def get_cities():
        request = requests.post(CITIES_URL, headers=HEADERS)
        return request.json()['result']

    def get_city(self, city_name):
        city = next((i for i in self.get_cities() if i['name'] == city_name), None)
        return city

    def get_schedules(self, city_name):
        city_id = self.get_city(city_name)['objectId']
        request = requests.get(MENU_URL.format(city_id), headers=HEADERS, cookies=self.cookies)
        return request.json()['schedules']

    def get_schedule_by_restaurant_name(self, restaurant_name, city_name):
        restaurant = next(
            i
            for i in self.get_schedules(city_name)
            if i['restaurant']['name'] == restaurant_name
        )
        return restaurant

    def get_schedule_by_meal_name(self, meal_name, city_name):
        try:
            return next(i for i in self.get_schedules(city_name) if i['meal']['name'] == meal_name)
        except StopIteration:
            raise Exception("No meal {} from {} today!".format(meal_name, city_name))

    def reserve_meal(
            self,
            timing,
            city_name,
            restaurant_name=None,
            meal_name=None,
            cancel_current_meal=False,
    ):  # pylint: disable=too-many-arguments
        assert restaurant_name or meal_name
        if cancel_current_meal:
            self.cancel_current_meal()

        if meal_name:
            schedule_id = self.get_schedule_by_meal_name(meal_name, city_name)['id']
        else:
            schedule_id = self.get_schedule_by_restaurant_name(restaurant_name, city_name)['id']

        reserve_data = {
            'quantity': 1,
            'schedule_id': schedule_id,
            'pickup_time': timing,
            'source': 'Web',
        }

        request = requests.post(RESERVATION_URL, data=json.dumps(reserve_data), headers=HEADERS, cookies=self.cookies)
        return request.status_code

    def get_current_meal(self):
        request = requests.post(KITCHEN_URL, headers=HEADERS, cookies=self.cookies)
        return request.json()

    def cancel_current_meal(self):
        raise NotImplementedError()


def execute_reserve_meal(mealpal, MEALS):

    # Try to login
    while True:
        status_code = mealpal.login()
        if status_code == 200:
            print('Logged In!')
            break
        else:
            print('Login Failed! Retrying...')

    # Once logged in, try to reserve meal
    for meal in MEALS:
        restaurant_name = meal['restaurant_name']
        meal_name = meal['meal_name']
        print('Trying to reserve {} from {}'.format(meal_name, restaurant_name))
        try:
            status_code = mealpal.reserve_meal(
                '12:00pm-12:15pm',
                city_name='Seattle',
                restaurant_name=restaurant_name,
                meal_name=meal_name
            )
            if status_code == 200:
                print("Successfully reserved {} from {}".format(meal_name, restaurant_name))
                return "Successfully reserved {} from {}".format(meal_name, restaurant_name)
            else:
                print('Reservation error, retrying!')
        except Exception as e:
            print('Retrying because the following exception: ', e)
            time.sleep(0.05)

    return "Did not find anything you like in the menu!"

def mealpal_handler(event, context):
    configurations = load_config()
    EMAIL = configurations['email_address']
    MEALS = configurations['meals']

    encrypted = environ['password']
    PASSWORD = boto3.client('kms').decrypt(CiphertextBlob=b64decode(encrypted))['Plaintext'].decode('utf-8')

    mealpal = MealPal(EMAIL, PASSWORD)

    return execute_reserve_meal(mealpal, MEALS)

if __name__ == "__main__":
    mealpal_handler('', '')
