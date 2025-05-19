import requests
import logging
import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
import asyncio
import re
import os
from config import PLANKA_URL, USERNAME, PASSWORD, TELEGRAM_TOKEN, ALLOWED_USERS, BOARD_IDS, TIMEZONE

logging.basicConfig(
    level=logging.INFO,
    filename="bot.log",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Function: clears the log if it is older than 7 days
def clear_log():
    LOG_FILE = "bot.log"
    if os.path.exists(LOG_FILE):
        file_age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(LOG_FILE))).days
        if file_age >= 7:
            open(LOG_FILE, "w").close()

# Function: obtaining Bearer token in Planka
def get_token():
    clear_log()
    url = f"{PLANKA_URL}/access-tokens"
    payload = {"emailOrUsername": USERNAME, "password": PASSWORD}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()["item"]
    except requests.RequestException as e:
        logging.error(f"Error when receiving a token: {e}")
        return None

# Function: clears the comment from unnecessary spaces and line breaks
def clean_comment(text):
    return re.sub(r"\s+", " ", text).strip()

# Function: retrieves the last comment in the Planka card
def get_last_comment(token, card_id):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{PLANKA_URL}/cards/{card_id}/actions"
    try:
        response = requests.get(url, headers=headers).json()
        
        if "items" not in response:
            return "" 

        comments = [
            {
                "text": clean_comment(c["data"]["text"]),
                "createdAt": datetime.datetime.fromisoformat(c["createdAt"].replace("Z", ""))
            }
            for c in response["items"] if c["type"] == "commentCard"
        ]

        if comments:
            comments.sort(key=lambda x: x["createdAt"], reverse=True)
            return f'{comments[0]["text"][:50]}'  # Limit 50 characters
        return ""
    except requests.RequestException:
        return ""
    except ValueError:
        return ""

# Function: receiving cards with their deadlines
def get_due_cards(token, start_date, end_date):
    headers = {"Authorization": f"Bearer {token}"}
    result = {}

    projects_url = f"{PLANKA_URL}/projects"
    projects_response = requests.get(projects_url, headers=headers).json()
    projects = projects_response["items"]

    for project in projects:
        project_name = project["name"]
        project_id = project["id"]

        boards_url = f"{PLANKA_URL}/projects/{project_id}"
        boards_response = requests.get(boards_url, headers=headers).json()
        boards = boards_response["included"]["boards"]

        filtered_boards = [b for b in boards if b["id"] in BOARD_IDS]

        for board in filtered_boards:
            board_name = board["name"]
            board_id = board["id"]

            board_url = f"{PLANKA_URL}/boards/{board_id}"
            board_response = requests.get(board_url, headers=headers).json()

            included_lists = board_response["included"]["lists"]
            valid_list_ids = {lst["id"] for lst in included_lists}
            
            cards = board_response["included"]["cards"]

            for card in cards:
                if "listId" not in card or card["listId"] not in valid_list_ids:
                    continue
                
                due_date = card.get("dueDate")
                completed = card.get("isDueDateCompleted", False)

                if due_date and not completed:
                    due_date_obj = datetime.datetime.fromisoformat(due_date.replace("Z", "")).replace(tzinfo=datetime.timezone.utc).astimezone(TIMEZONE)
                    due_date_date = due_date_obj.date()

                    if start_date <= due_date_date <= end_date:
                        short_name = (card["name"][:30] + "...") if len(card["name"]) > 30 else card["name"]
                        comment = get_last_comment(token, card["id"])

                        if project_name not in result:
                            result[project_name] = {}
                        if board_name not in result[project_name]:
                            result[project_name][board_name] = []
                        
                        result[project_name][board_name].append(
                            (short_name, due_date_obj.strftime("%d-%m-%Y %H:%M"), comment)
                        )
    return result

# Authorisation in the Telegram bot
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Setting up buttons in the Telegram bot
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗓 Tasks for today")],
        [KeyboardButton(text="🗓 Tomorrow's tasks")],
        [KeyboardButton(text="🗓 Tasks for the week")],
        [KeyboardButton(text="🗓 Date tasks")]
    ],
    resize_keyboard=True
)

# Handler for the /start command
@dp.message(Command("start"))
async def start(message: types.Message):
    if message.from_user.id not in ALLOWED_USERS: # ALLOWED_USERS check
        return await message.reply("You're not allowed access to the bot") 
    await message.reply("Hi. Choose an action by clicking on the button below to get information from your Planka", reply_markup=keyboard)

# Button handler
@dp.message(F.text.in_(["🗓 Tasks for today", "🗓 Tomorrow's tasks", "🗓 Tasks for the week"]))
async def send_tasks(message: types.Message):
    today = datetime.datetime.now(TIMEZONE).date()
    if "today" in message.text:
        start_date = datetime.date.min
        end_date = today
    elif "tomorrow" in message.text:
        start_date = today + datetime.timedelta(days=1)
        end_date = today + datetime.timedelta(days=1)
    else:
        start_date = datetime.date.min
        end_date = today + datetime.timedelta(days=7)

    token = get_token() # Request a new token before each request
    tasks = get_due_cards(token, start_date, end_date)

    if not tasks:
        return await message.reply("Congratulations! There are no tasks for the selected period!")

    response_text = ""
    for project, boards in tasks.items():
        response_text += f"\n----- Project: <b>{project}</b> -----\n"
        for board, cards in boards.items():
            response_text += f"\nBoard: <b>{board}</b>\n"
            for name, date, comment in cards:
                response_text += f"\n📄 Card: {name}(🕒 {date})\n"
                if comment:
                    response_text += f"<i>💬 Comment: {comment}</i>\n"
            response_text += "---\n"

    await message.reply(response_text.strip() or "Congratulations! There are no tasks for the selected period!", reply_markup=keyboard, parse_mode="HTML")

# Handler of the ‘Tasks on date’ button
@dp.message(F.text == "🗓 Date tasks")
async def ask_date(message: types.Message):
    await message.reply("Enter the date in DD-MM-YYYYY format (e.g. 10-06-2025)")

@dp.message(F.text.regexp(r"\d{2}-\d{2}-\d{4}"))
async def tasks_by_date(message: types.Message):
    try:
        target_date = datetime.datetime.strptime(message.text, "%d-%m-%Y").date()
    except ValueError:
        return await message.reply("Incorrect date format. Use DD-MM-YYYYY (be sure to use the '-' character)")

    token = get_token()
    tasks = get_due_cards(token, target_date, target_date)

    if not tasks:
        return await message.reply("Congratulations! There are no tasks for the selected period!")

    response_text = ""
    for project, boards in tasks.items():
        response_text += f"\n----- Project: <b>{project}</b> -----\n"
        for board, cards in boards.items():
            response_text += f"\nBoard: <b>{board}</b>\n"
            for name, date, comment in cards:
                response_text += f"\n📄 Card: {name}(🕒 {date})\n"
                if comment:
                    response_text += f"<i>Comment: {comment}</i>\n"
            response_text += "---\n"

    await message.reply(response_text.strip() or "Congratulations! There are no tasks for the selected period!", reply_markup=keyboard, parse_mode="HTML")

# Launching Telegram bot
async def main():
    while True:
        try:
            await dp.start_polling(bot, timeout=60)
        except Exception as e:
            logging.error(f"Bot error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
