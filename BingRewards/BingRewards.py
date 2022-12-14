import shlex
import sys
import os
import logging
import base64
import json
import random
import subprocess
from requests import get
from time import sleep
from options import parse_search_args
from src.rewards import Rewards
from src.log import HistLog, StatsJsonLog
from src.messengers import TelegramMessenger, DiscordMessenger
from src.google_sheets_reporting import GoogleSheetsReporting

LOG_DIR = "logs"
ERROR_LOG = "error.log"
RUN_LOG = "run.json"
SEARCH_LOG = "search.json"
STATS_LOG = "stats.json"
CONFIG_FILE_PATH = "config/config.json"
DEBUG = True


def connect_vpn():
    config = get_config()
    vpn_command = 'echo "{}" | openconnect {} --user={} --passwd-on'.format(config["cisco_password"], config["cisco_server"], config["cisco_username"])
    # subprocess.Popen(shlex.split(vpn_command))
    subprocess.Popen(vpn_command, shell=True)

def disconnect_vpn():
    os.system("sudo killall openconnect")

def get_host_ip():
    return get('https://api.ipify.org').content.decode('utf8')

def use_vpn():
    try:
        if get_config()["cisco_server"] != "":
            return True
    except:
        return False
    return False

def has_ip_changed(host_ip):
    current_ip = get_host_ip()
    if host_ip == current_ip:
        print('>> System valid IP address is: {}. Retrying...'.format(current_ip))
        return False

    print('>> Connected to the VPN server. IP: {}'.format(current_ip))
    return True

def _log_hist_log(hist_log):
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(message)s',
        filename=os.path.join(LOG_DIR, ERROR_LOG)
    )
    logging.exception(hist_log.get_timestamp())
    logging.debug("")


def __decode(encoded):
    if encoded:
        return base64.b64decode(encoded).decode()


def get_config():
    if os.path.isfile(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH) as f:
                config = json.load(f)
        except ValueError:
            print("There was an error decoding the 'config.json' file")
            raise
    else:
        raise ImportError(
            "'config.json' file does not exist. Please run `python setup.py`.\nIf you are a previous user, existing credentials will be automatically ported over.")
    return config

def get_vpn_config(config):
    cisco_username = config.get('cisco_username')
    cisco_password = config.get('cisco_password')
    cisco_server = config.get('cisco_server')

    return cisco_server, cisco_username, cisco_password

def get_telegram_messenger(config):
    telegram_api_token = config.get('telegram_api_token')
    telegram_userid = config.get('telegram_userid')
    telegram_messenger = None

    if not telegram_api_token or not telegram_userid:
        print('You have selected Telegram, but config file is missing `api token` or `userid`. Please re-run setup.py with additional arguments if you want Telegram notifications.')
    else:
        telegram_messenger = TelegramMessenger(telegram_api_token, telegram_userid)
    return telegram_messenger


def get_discord_messenger(config, args):
    discord_webhook_url = __decode(config.get('discord_webhook_url'))
    discord_messenger = None

    if not args.discord or not discord_webhook_url:
        if args.discord:
            print('You have selected Discord, but the config file is missing a webhook_url. Please re-run setup.py with additional arguments if you want Discord notifications.')
    else:
        discord_messenger = DiscordMessenger(discord_webhook_url)
    return discord_messenger


def get_google_sheets_reporting(config, args):
    sheet_id = __decode(config.get('google_sheets_sheet_id'))
    tab_name = __decode(config.get('google_sheets_tab_name'))

    if args.google_sheets and sheet_id and tab_name:
        google_sheets_reporting = GoogleSheetsReporting(sheet_id, tab_name)
    else:
        if args.google_sheets:
            print('You have selected Google Sheets reporting, but main config file is missing sheet_id or tab_name. Please re-run setup.py with additional arguments if you want Google Sheets reporting.')
        google_sheets_reporting = None
    return google_sheets_reporting


def complete_search(rewards, completion, search_type, search_hist):
    print("######## You selected {}".format(search_type))
    if not completion.is_search_type_completed(search_type):
        rewards.complete_search_type(search_type, completion, search_hist)
    else:
        print(f'{search_type.capitalize()} already completed\n')


def main():
    # change to top dir
    dir_run_from = os.getcwd()
    top_dir = os.path.dirname(sys.argv[0])
    if top_dir and top_dir != dir_run_from:
        os.chdir(top_dir)

    config = get_config()

    args = parse_search_args()
    if args.email and args.password:
        email = args.email
        password = args.password
        args.cookies = False
    else:
        pass
        # email = __decode(config['user'][0]['email'])
        # password = __decode(config['user'][0]['password'])

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    # Setup telegram notifier


    # Shuffle users before start searching
    users = config['user']
    random.shuffle(users)

    for user in config['user']:
        email = user['email']
        password = user['password']
        print("######## Start rewarding for: {}".format(email))

        # telegram_messenger = get_telegram_messenger(config, args)
        # if telegram_messenger is not None:
        #     telegram_messenger.send_message("Start rewarding for {}".format(email))

        try:
            # Read last run logs
            stats_log = StatsJsonLog(os.path.join(LOG_DIR, STATS_LOG), email)
            hist_log = HistLog(email, os.path.join(LOG_DIR, RUN_LOG), os.path.join(LOG_DIR, SEARCH_LOG))

            completion = hist_log.get_completion()
            search_hist = hist_log.get_search_hist()

            telegram_messenger = get_telegram_messenger(config)
            if telegram_messenger is not None:
                messenger = [telegram_messenger]

            rewards = Rewards(email, password, DEBUG, args.headless, args.cookies,
                            args.driver, args.nosandbox, args.google_trends_geo, messenger)

            complete_search(rewards, completion, args.search_type, search_hist)
            hist_log.write(rewards.completion)
            completion = hist_log.get_completion()

            print("######## Initial: {} Final: {}".format(rewards.init_points, rewards.final_points))

            telegram_messenger = get_telegram_messenger(config)
            if telegram_messenger is not None:
                telegram_messenger.send_message("End of rewarding for {} \nInitial:{} Final: {}".format(email, rewards.init_points, rewards.final_points))

            if hasattr(rewards, 'stats'):
                formatted_stat_str = "; ".join(rewards.stats.stats_str)
                stats_log.add_entry_and_write(formatted_stat_str, email)

                run_hist_str = hist_log.get_run_hist()[-1].split(': ')[1]

                telegram_messenger = get_telegram_messenger(config)
                if telegram_messenger is not None:
                    telegram_messenger.send_reward_message(rewards.stats.stats_str, run_hist_str, email)

            # check again, log if any failed
            if not completion.is_search_type_completed(args.search_type):
                logging.basicConfig(
                    level=logging.DEBUG,
                    format='%(message)s',
                    filename=os.path.join(LOG_DIR, ERROR_LOG)
                )
                logging.debug(hist_log.get_timestamp())
                for line in rewards.stdout:
                    logging.debug(line)
                logging.debug("")

        except Exception as e:
            print(">>>>> Exception countered: ", e)

        # Sleep for a random moments before start for new account
        sleep_time = random.randint(30, 300)
        print(">> Sleeping {}s".format(sleep_time))
        sleep(sleep_time)

if __name__ == "__main__":
    if use_vpn():
        host_ip = get_host_ip()
        connect_vpn()

        counter = 0
        while has_ip_changed(host_ip) is False:
            sleep(5)
            counter += 1

            if counter >= 10:
                print(">> VPN not connected properly. Exit.")
                exit()

    main()

    if use_vpn():
        disconnect_vpn()
