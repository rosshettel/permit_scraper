"""Scrapes recreation.gov and sends an email when permits are available.

The script expects a local smtp server to have been setup for email
notifications.

Sample commands:
1) For recreation.gov
python3 scrape.py --scrape_interval_secs=600 --email_addr=foo@bar.com \
        --mode=permits --chrome_user_data_dir=/home/user/.config/chrome/Profile/

2) To get notifications about WA ferries
python3 scrape.py --scrape_interval_secs=60 --email_addr=foo@bar.com \
        --mode=ferry --ferry_from="Orcas Island" --ferry_to="Anacortes" \
        --ferry_depart_after="10:00 AM" --ferry_depart_before="4:00 PM" \
        --ferry_date="07052020"

3) To get notifications about Core enchantment permit availability for the
   months of July/August
python3 scrape.py --scrape_interval_secs=60 --email_addr=foo@bar.com \
        --permit_api_months_to_query="2020-07-01,2020-08-01" \
        --mode=permits_json

"""

from datetime import datetime as dt
from datetime import timedelta
import email.message
import email.utils
import smtplib
import time

from absl import flags
from absl import app
import pytz
import requests
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.wait import WebDriverWait

FLAGS = flags.FLAGS
flags.DEFINE_enum("mode", "permits", ["permits", "permits_json", "ferry"],
                  "Which mode to run the script in")
flags.DEFINE_bool("headless", True, "If true, runs chrome in headless mode")
flags.DEFINE_string(
    "chrome_user_data_dir", "/path/to/dir",
    "Using an existing chrome profile will allow the browser to be logged into "
    "recreation.gov (or other sites)")
flags.DEFINE_integer("scrape_interval_secs", 10,
                     "How often to run the scraping")
flags.DEFINE_string("email_addr", None, "Where to send the email notification")
flags.DEFINE_string(
    "permit_availability_url",
    "https://www.recreation.gov/permits/233273/registration/detailed-availability",
    "Link to the recreation.gov's detailed availability page for the desired "
    "permit.")
flags.DEFINE_list(
    "permit_dates_to_add_to_cart", "07/17,07/16",
    "If any of these days is available, add to cart and sleep for 15 minutes. "
    "Only do this if running with a browser (not headless)")
flags.DEFINE_integer("permit_desired_slots", 3,
                     "Desired number of permits to add to cart")

# Flags for the ferry mode
flags.DEFINE_string(
    "ferry_url",
    "https://secureapps.wsdot.wa.gov/ferries/reservations/Vehicle/SailingSchedule.aspx",
    "Link to wsdot ferry reservation page")
flags.DEFINE_string("ferry_from", "Anacortes", "Ferry Start")
flags.DEFINE_string("ferry_to", "Orcas Island", "Ferry End")
flags.DEFINE_string("ferry_depart_after", "12:00 PM",
                    "Only interested in ferries leaving after this time")
flags.DEFINE_string("ferry_depart_before", "6:00 PM",
                    "Only interested in ferries leaving before this time")
flags.DEFINE_string("ferry_date", "07052020",
                    "Date for ferry departure (Format: MMDDYYYY)")

# Flags for the permit JSON mode
flags.DEFINE_string(
    "permit_api_url",
    "https://www.recreation.gov/api/permits/233273/availability/month",
    "Base URL to query monthly availability")
flags.DEFINE_list(
    "permit_api_months_to_query", "2020-07-01,2020-08-01",
    "First day of the month for months that need to be queried for permit "
    "availability")

# Only notify once per date
skip_notification_date_set = set()


def send_email(to_email, subject, body):
  if FLAGS.email_addr is None:
    print("Please specify email_addr to send email notifications")
    return
  msg = email.message.Message()
  msg['From'] = to_email
  msg['To'] = to_email
  msg['Subject'] = subject
  msg.add_header('Content-Type', 'text')
  msg.set_payload(body)

  print("Sending email with subject '%s' to %s" % (subject, to_email))
  smtp_obj = smtplib.SMTP("localhost")
  smtp_obj.sendmail(msg['From'], [msg['To']], msg.as_string())
  smtp_obj.quit()


def maybe_send_notification(date_set):
  filtered_dates = sorted(date_set - skip_notification_date_set)
  skipped_dates = sorted(date_set.intersection(skip_notification_date_set))
  if len(skipped_dates) > 0:
    print("Skipping already notified dates " + ", ".join(skipped_dates))
  if len(filtered_dates) == 0:
    return
  subject = "Found %s availability for %s" % (FLAGS.mode,
                                              ", ".join(filtered_dates))
  send_email(FLAGS.email_addr, subject, subject)
  skip_notification_date_set.update(filtered_dates)


def select_permit_options(driver):
  overnight = Select(
      driver.find_element_by_xpath('//*[@id="division-selection"]'))
  overnight.select_by_visible_text("Overnight")

  time.sleep(1)
  people = Select(
      driver.find_element_by_xpath('//*[@id="quota-view-selector"]'))
  people.select_by_visible_text("People")

  # Select number of people in the group
  group_dropdown = driver.find_element_by_xpath(
      '//*[@id="guest-counter-QuotaUsageByMember"]/span[1]')
  group_dropdown.click()
  group_size = driver.find_element_by_xpath(
      '//*[@id="guest-counter-QuotaUsageByMember-popup"]/div/div[1]/div/div/div[1]/div[2]/div/div/button[2]'
  )
  # Look for permits for the requested number of people
  for _ in range(0, FLAGS.permit_desired_slots):
    group_size.click()
  group_dropdown.click()


def maybe_add_to_cart_and_sleep(driver, availability_map):
  if len(availability_map) == 0:
    return

  book_date = None
  for date in FLAGS.permit_dates_to_add_to_cart:
    if date in availability_map.keys():
      book_date = date
      break

  if book_date is None:
    print("Requested dates unavailable. Not adding to cart")
    return

  print("Requested date (%s) available. Trying to book..." % book_date)
  permit_val = driver.find_element_by_xpath(availability_map[book_date])
  permit_val.click()

  book_button = driver.find_element_by_xpath(
      '//*[@id="per-availability-main"]/div/div[1]/div[3]/div[3]/div/div/div/button'
  )
  book_button.click()

  #time.sleep(5)
  WebDriverWait(driver, 5).until(
      EC.presence_of_element_located((By.ID, 'add-permit-to-cart-button')))
  end_dt = dt.strptime(book_date + "/2020", "%m/%d/%Y") + timedelta(days=2)
  exit_elem = driver.find_element_by_id('exitDateCalendar')
  exit_elem.click()
  exit_elem.send_keys(end_dt.strftime("%m/%d/%Y"))

  agree_elem = driver.find_element_by_xpath(
      '//*[@id="form-name"]/fieldset/section/div[3]/label/span')
  agree_elem.click()

  while True:
    add_to_cart_button = driver.find_element_by_id('add-permit-to-cart-button')
    actions = ActionChains(driver)
    actions.click(add_to_cart_button)
    actions.perform()
    print("Added permit to cart. Sleeping for 500 seconds before modifying...")
    time.sleep(500)
    modify_elem = driver.find_element_by_xpath(
        '//*[@id="page-body"]/div/div/div/div[1]/div[1]/div/div[2]/div[1]/div[4]/button[1]/span'
    )
    modify_elem.click()
    WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.ID, "exitDateCalendar")))


def permit_loop(driver):
  num = 0
  while True:
    print("Running scraping loop %d" % num)
    num += 1
    est_tz = pytz.timezone("US/Eastern")
    est_now = dt.now(tz=est_tz)
    today_date = est_now.strftime("%Y-%m-%d")
    driver.get("%s?date=%s" % (FLAGS.permit_availability_url, today_date))
    # TODO: For some reason the page doesn't fully load every so often. Catch
    # the exception and simply retry.
    try:
      select_permit_options(driver)
      print("Waiting 5 seconds for the dynamic table to load...")
      time.sleep(5)

      # Read 7 days of permit info.
      availability_map = {}
      for ii in range(2, 9):
        val_xpath = ('//*[@id="per-availability-main"]/div/div[1]/div[3]/div[2]'
                     '/div/table/tbody/tr[5]/td[%d]') % ii

        val = driver.find_element_by_xpath(val_xpath)
        if val.text == '':
          continue
        num_slots = int(val.text)
        if num_slots > 0:
          day_month_str = (est_now + timedelta(days=(ii - 2))).strftime("%m/%d")
          print("Found availability on %s" % day_month_str)
          availability_map[day_month_str] = val_xpath
      maybe_send_notification(set(availability_map.keys()))
    except Exception as e:
      print("Error: %s. Rerunning loop..." % str(e))
      time.sleep(1)
      continue

    # Try this separately as we may have started booking the reservation and we
    # don't want reload the page. Give the user at least 10 minutes to finish
    # booking.
    try:
      maybe_add_to_cart_and_sleep(driver, availability_map)
    except Exception as e:
      print("Error: %s. Rerunning loop..." % str(e))
      time.sleep(600)
      continue

    print("Sleeping %d seconds before running next loop..." % \
            FLAGS.scrape_interval_secs)
    time.sleep(FLAGS.scrape_interval_secs)


def select_ferry_options(driver):
  start = Select(
      driver.find_element_by_xpath('//*[@id="MainContent_dlFromTermList"]'))
  start.select_by_visible_text(FLAGS.ferry_from)
  time.sleep(2)
  end = Select(
      driver.find_element_by_xpath('//*[@id="MainContent_dlToTermList"]'))
  end.select_by_visible_text(FLAGS.ferry_to)
  time.sleep(2)
  date = driver.find_element_by_xpath('//*[@id="MainContent_txtDatePicker"]')
  date.click()
  date.send_keys(Keys.CONTROL, 'a')
  date.send_keys(Keys.BACKSPACE)
  date.send_keys(FLAGS.ferry_date)
  date.send_keys(Keys.ESCAPE)

  vehicle_size = Select(
      driver.find_element_by_xpath('//*[@id="MainContent_dlVehicle"]'))
  vehicle_size.select_by_index(2)
  time.sleep(2)
  vehicle_height = Select(
      driver.find_element_by_xpath('//*[@id="MainContent_ddlCarTruck14To22"]'))
  vehicle_height.select_by_index(1)
  time.sleep(2)
  show_avail = driver.find_element_by_xpath(
      '//*[@id="MainContent_btnContinue"]/h4')
  show_avail.click()
  time.sleep(2)


def ferry_reservation_loop(driver):
  num = 0
  time_fmt = '%I:%M %p'
  depart_after = dt.strptime(FLAGS.ferry_depart_after.strip(), time_fmt)
  depart_before = dt.strptime(FLAGS.ferry_depart_before.strip(), time_fmt)
  driver.get(FLAGS.ferry_url)
  select_ferry_options(driver)
  while True:
    print("Running scraping loop %d" % num)
    num += 1

    times_available = set()
    try:
      times = driver.find_elements_by_xpath(
          '//*[@id="MainContent_gvschedule"]/tbody/tr/td[2]')
      availability = driver.find_elements_by_xpath(
          '//*[@id="MainContent_gvschedule"]/tbody/tr/td[3]')
      for ii in range(len(times)):
        time_str = times[ii].text.strip()
        avail_str = availability[ii].text.strip()
        ferry_time = dt.strptime(time_str, time_fmt)
        if ferry_time < depart_after or ferry_time > depart_before:
          continue
        if avail_str.find("Space Available") >= 0:
          times_available.add(time_str)
    except Exception as e:
      print("Error: %s. Rerunning loop after sleeping 1 minute..." % str(e))
      time.sleep(60)
      continue
    maybe_send_notification(times_available)
    print("Sleeping %d seconds before running next loop..." % \
            FLAGS.scrape_interval_secs)
    time.sleep(FLAGS.scrape_interval_secs)
    refresh = driver.find_element_by_xpath(
        '//*[@id="MainContent_btnRefresh"]/h4')
    refresh.click()
    time.sleep(2)


def permit_json_loop():
  num = 0
  while True:
    print("Running scraping loop %d" % num)
    num += 1
    headers = {}
    headers["content-type"] = "application/json"
    headers["cache-control"] = "no-cache, no-store, must-revalidate"
    headers["user-agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/83.0.4103.106 Safari/537.36")

    available_date_set = set()
    for start_date in FLAGS.permit_api_months_to_query:
      url = "%s?start_date=%sT00:00:00.000Z" % (FLAGS.permit_api_url,
                                                start_date)
      response = None
      try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
          print("Error fetching URL %s: Received HTTP status code %s" %
                (url, str(response.status_code)))
          continue
      except Exception as e:
        print("Error: %s. Rerunning loop after sleeping 1 minute..." % str(e))
        time.sleep(60)
        continue
      avail_json = response.json()
      all_availability = avail_json['payload']['availability']
      core_availability = all_availability['30']['date_availability']
      for k, v in core_availability.items():
        date = k[0:10]
        if v['remaining'] > 0:
          available_date_set.add(date)

    maybe_send_notification(available_date_set)
    print("Sleeping %d seconds before running next loop..." % \
            FLAGS.scrape_interval_secs)
    time.sleep(FLAGS.scrape_interval_secs)


def main(_):
  if FLAGS.mode == "permits_json":
    permit_json_loop()
    return

  opts = Options()
  opts.add_argument('--user-data-dir=%s' % FLAGS.chrome_user_data_dir)
  if FLAGS.headless:
    opts.add_argument('--headless')
  driver = webdriver.Chrome(options=opts)
  driver.set_window_size(1920, 1080)
  driver.implicitly_wait(3)
  if FLAGS.mode == "permits":
    permit_loop(driver)
  else:
    ferry_reservation_loop(driver)


if __name__ == "__main__":
  app.run(main)
