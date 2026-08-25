import requests
from icalendar import Calendar
import os
from dotenv import load_dotenv
from datetime import datetime
now = datetime.now()
load_dotenv()
CALENDAR_URL = os.getenv("CALENDAR_URL")
response = requests.get(CALENDAR_URL)
cal = Calendar.from_ical(response.content)

# Date format should be YYYY-MM-DD
def get_events(start_date=None, end_date=None):

    start_date = start_date if start_date is None else datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date = end_date if end_date is None else datetime.strptime(end_date, "%Y-%m-%d").date()

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        dtstart = component.get("dtstart")

        if dtstart is None:
            continue

        assignment_date = dtstart.dt

        if isinstance(assignment_date, datetime):
            assignment_date = assignment_date.date()

        if start_date <= assignment_date <= end_date:
            print(component.get("summary"))
            print(dtstart.dt)

def main():
    start_date = input("Enter start date (YYYY-MM-DD) or leave blank for no start date: ")
    end_date = input("Enter end date (YYYY-MM-DD) or leave blank for no end date: ")

    start_date = start_date if start_date else None
    end_date = end_date if end_date else None

    get_events(start_date, end_date)

if __name__ == "__main__":
    main()
