import datetime
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Union


CALENDAR_DATA_FILE = Path(__file__).with_name("calendar_data.json")
DAY_IS_EMPTY = "Day is empty"


def _hhmm_to_minutes(value: int) -> int:
    """Validate a 24-hour HHMM integer and return minutes after midnight."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Time must be an integer in 24-hour HHMM format.")

    hours, minutes = divmod(value, 100)
    if value < 0 or hours > 23 or minutes > 59:
        raise ValueError(f"Invalid 24-hour HHMM time: {value}.")

    return (hours * 60) + minutes


def _load_calendar_data() -> dict:
    if not CALENDAR_DATA_FILE.exists():
        return {}

    try:
        with CALENDAR_DATA_FILE.open("r", encoding="utf-8") as data_file:
            data = json.load(data_file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Calendar data is malformed: {CALENDAR_DATA_FILE}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            f"Calendar data must contain a JSON object: {CALENDAR_DATA_FILE}"
        )

    return data


def _write_calendar_data(data: dict) -> None:
    # Write through a temporary file to avoid leaving partial JSON
    temporary_file = CALENDAR_DATA_FILE.with_suffix(
        CALENDAR_DATA_FILE.suffix + ".tmp"
    )
    with temporary_file.open("w", encoding="utf-8") as data_file:
        json.dump(data, data_file, indent=2, sort_keys=True)
        data_file.write("\n")
    temporary_file.replace(CALENDAR_DATA_FILE)

def _load_day_record(requested_date: datetime.date) -> tuple[list, list]:
    """Return raw blocks and tasks, accepting the original list-only format."""
    stored_day = _load_calendar_data().get(requested_date.isoformat(), {})

    # Original files stored the day's time blocks directly as a list.
    if isinstance(stored_day, list):
        return stored_day, []

    if not isinstance(stored_day, dict):
        raise ValueError(f"Stored day {requested_date.isoformat()} is malformed.")

    stored_blocks = stored_day.get("time_blocks", [])
    stored_tasks = stored_day.get("tasks", [])
    if not isinstance(stored_blocks, list) or not isinstance(stored_tasks, list):
        raise ValueError(f"Stored day {requested_date.isoformat()} is malformed.")
    return stored_blocks, stored_tasks

@dataclass
class Task:
    name: str
    Time_Due: int
    description: str

    def __post_init__(self) -> None:
        _hhmm_to_minutes(self.Time_Due)

    @property
    def time_due(self) -> int:
        """PEP 8-style alias while preserving the original field name."""
        return self.Time_Due
    
def _load_tasks(requested_date: datetime.date) -> list[Task]:
    """Load and validate all saved tasks for one date."""
    _, stored_tasks = _load_day_record(requested_date)

    try:
        tasks = [
            Task(
                name=task["name"],
                Time_Due=task.get("Time_Due", task.get("time_due")),
                description=task["description"],
            )
            for task in stored_tasks
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Stored tasks for {requested_date.isoformat()} are malformed."
        ) from error

    tasks.sort(key=lambda task: _hhmm_to_minutes(task.Time_Due))
    return tasks
    
@dataclass
class TimeBlock:
    name: str
    start_time: int
    end_time: int
    description: str

    def __post_init__(self) -> None:
        start_minutes = _hhmm_to_minutes(self.start_time)
        end_minutes = _hhmm_to_minutes(self.end_time)
        if end_minutes <= start_minutes:
            raise ValueError(
                "A time block must end later on the same day than it starts."
            )


def _load_time_blocks(requested_date: datetime.date) -> list[TimeBlock]:
    """Load and validate all saved blocks for one date."""
    stored_blocks, _ = _load_day_record(requested_date)

    try:
        blocks = [
            TimeBlock(
                name=block["name"],
                start_time=block["start_time"],
                end_time=block["end_time"],
                description=block["description"],
            )
            for block in stored_blocks
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Stored day {requested_date.isoformat()} is malformed."
        ) from error

    blocks.sort(key=lambda block: _hhmm_to_minutes(block.start_time))
    return blocks


class Day:
    def __init__(self, day: int, month: int, year: int):
        self.date = datetime.date(year, month, day)
        self.day = day
        self.month = month
        self.year = year
        self.month_name = self.date.strftime("%B")
        self.day_name = self.date.strftime("%A")
        self.time_blocks = _load_time_blocks(self.date)
        self.tasks = _load_tasks(self.date)
        self.storage_key = self.date.isoformat()

    def add_timeblock(
        self,
        name: str,
        start_time: int,
        end_time: int,
        description: str,
    ) -> bool:
        
        new_block = TimeBlock(name, start_time, end_time, description)
        new_start = _hhmm_to_minutes(new_block.start_time)
        new_end = _hhmm_to_minutes(new_block.end_time)

        for existing_block in self.time_blocks:
            existing_start = _hhmm_to_minutes(existing_block.start_time)
            existing_end = _hhmm_to_minutes(existing_block.end_time)

            # Dont allow overlap
            if new_start < existing_end and new_end > existing_start:
                
                return False

        self.time_blocks.append(new_block)
        self.time_blocks.sort(key=lambda block: _hhmm_to_minutes(block.start_time))
        export_day(self)
        return True

    def add_task(
        self,
        name: str,
        time_due: int,
        description: str,
    ) -> bool:
        """Add a task to this date and save it immediately."""
        new_task = Task(name, time_due, description)
        self.tasks.append(new_task)
        self.tasks.sort(key=lambda task: _hhmm_to_minutes(task.Time_Due))
        export_day(self)
        return True


def export_day(calendar_day: Day) -> None:

    calendar_day.time_blocks.sort(
        key=lambda block: _hhmm_to_minutes(block.start_time)
    )
    calendar_day.tasks.sort(
        key=lambda task: _hhmm_to_minutes(task.Time_Due)
    )
    data = _load_calendar_data()
    data[calendar_day.storage_key] = {
        "time_blocks": [
            asdict(block) for block in calendar_day.time_blocks
        ],
        "tasks": [
            asdict(task) for task in calendar_day.tasks
        ],
    }
    _write_calendar_data(data)


def recall_day(year: int, month: int, day: int) -> Union[Day, str]:
    """Load a date, or return DAY_IS_EMPTY when it has no saved blocks."""
    recalled_day = Day(day, month, year)
    if not recalled_day.time_blocks and not recalled_day.tasks:
        return DAY_IS_EMPTY
    return recalled_day

if __name__ == "__main__":
    new_day = Day(6, 8, 2026)
    if new_day.add_timeblock("Movie2 ", 100, 200, "Short Spiderman"):
        print("Added")
    else:
        print("Failed to add")
