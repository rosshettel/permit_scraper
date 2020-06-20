"""Scrapes recreation.gov and sends an email when permits are available.

The script expects a local smtp server to have been setup for email
notifications.

Sample command:
python3 scrape.py --scrape_interval_secs=600 --email_addr=foo@bar.com

"""

from datetime import datetime as dt
from datetime import timedelta
import email.message
import email.utils
import smtplib
import time
import pytz

from absl import flags
from absl import app
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import Select

FLAGS = flags.FLAGS
flags.DEFINE_bool("headless", True, "If true, runs chrome in headless mode")
flags.DEFINE_integer("scrape_interval_secs", 10,
                     "How often to run the scraping")
flags.DEFINE_string("email_addr", None, "Where to send the email notification")
flags.DEFINE_string(
    "permit_availability_url",
    "https://www.recreation.gov/permits/233273/registration/detailed-availability",
    "Link to the recreation.gov's detailed availability page for the desired "
    "permit.")

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

  print("Sending email with subject %s to %s" % (subject, to_email))
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
  subject = "Found availability on " + ", ".join(filtered_dates)
  send_email(FLAGS.email_addr, subject, subject)
  skip_notification_date_set.update(filtered_dates)


def select_options(driver):
  overnight = Select(
      driver.find_element_by_xpath('//*[@id="division-selection"]'))
  overnight.select_by_visible_text('Overnight')

  # Select number of people in the group
  group_dropdown = driver.find_element_by_xpath(
      '//*[@id="guest-counter-QuotaUsageByMember"]/button/span[1]')
  group_dropdown.click()
  group_size = driver.find_element_by_xpath(
      '//*[@id="guest-counter-QuotaUsageByMember"]/div/div[1]/div/div[2]/div/div/button[2]'
  )
  # Look for permits for two people
  group_size.click()
  group_size.click()
  group_dropdown.click()


def run_in_loop(driver):
  num = 0
  while True:
    print("Running scraping loop %d" % num)
    num += 1
    driver.get(FLAGS.permit_availability_url)
    # TODO: For some reason the page doesn't fully load every so often. Catch
    # the exception and simply retry.
    try:
      select_options(driver)

      print("Waiting 5 seconds for the dynamic table to load...")
      time.sleep(5)

      est_tz = pytz.timezone("US/Eastern")
      est_now = dt.now(tz=est_tz)
      # Read 7 days of permit info.
      available_date_set = set()
      for ii in range(2, 9):
        val = driver.find_element_by_xpath(
            '//*[@id="per-availability-main"]/div/div[1]/div[3]/div[2]/div/table/tbody/tr[5]/td[%d]'
            % ii)
        if val.text != '' and int(val.text) > 0:
          date_str = (est_now + timedelta(days=ii)).strftime("%m/%d")
          print("Found availability on %s" % date_str)
          available_date_set.add(date_str)
    except Exception as e:
      print("Error: %s. Rerunning loop after sleeping 1 minute..." % str(e))
      time.sleep(60)
      continue

    maybe_send_notification(available_date_set)
    print("Sleeping %d seconds before running next loop..." % \
            FLAGS.scrape_interval_secs)
    time.sleep(FLAGS.scrape_interval_secs)


def main(_):
  opts = Options()
  if FLAGS.headless:
    opts.add_argument('--headless')
  driver = webdriver.Chrome(options=opts)
  driver.set_window_size(1920, 1080)
  driver.implicitly_wait(3)
  run_in_loop(driver)


if __name__ == "__main__":
  app.run(main)
