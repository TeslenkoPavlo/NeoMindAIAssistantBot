import asyncio
import os
import io
import uuid 
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

from motor.motor_asyncio import AsyncIOMotorClient
from aiogram.fsm.storage.mongo import MongoStorage

import google.generativeai as genai
import PIL.Image

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GENIMI_API_KEY = os.getenv("GENIMI_API_KEY")

MONGO_URL = os.getenv("MONGO_URL")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "gemini_bot_fsm") 

if not BOT_TOKEN or not GENIMI_API_KEY:
    print("Error: Could not load API keys. Check your .env file")
    exit()
if not MONGO_URL:
    print("Error: MONGO_URL is not set in .env file. Bot requires database.")
    exit()

genai.configure(api_key=GENIMI_API_KEY)

SYSTEM_PROMPTS = {
    "default": (
        "Act as a serious, professional assistant. "
        "Your answers must be short, specific, and to the point. "
        "FORBIDDEN: Do not use Markdown formatting (no **, *, #, ```, etc.). "
        "Respond in plain text only."
    ),
    "explanatory": (
        "Act as an explanatory assistant. "
        "Explain complex topics in simple terms, using analogies. "
        "Be detailed and break down your answer into logical steps. "
        "FORBIDDEN: Do not use Markdown formatting (no **, *, #, ```, etc.). "
        "Respond in plain text only."
    ),
    "learning": (
        "Act as a Socratic tutor. "
        "Help the user learn by asking guiding questions. "
        "Do not give the answer directly unless asked. Encourage the user to think. "
        "FORBIDDEN: Do not use Markdown formatting (no **, *, #, ```, etc.). "
        "Respond in plain text only."
    ),
    "formal": (
        "Act as a formal, official assistant. "
        "Use precise, professional, and very polite language. Avoid contractions. "
        "Structure your answers clearly. "
        "FORBIDDEN: Do not use Markdown formatting (no **, *, #, ```, etc.). "
        "Respond in plain text only."
    )
}
DEFAULT_STYLE = "default"

AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite"
]
DEFAULT_MODEL = AVAILABLE_MODELS[1] 

router = Router()
bot: Bot = None 


@router.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    """Handler for /start command. Explains bot features."""
    
    data = await state.get_data()
    if "user_model" not in data:
        data["user_model"] = DEFAULT_MODEL
    if "user_style" not in data:
        data["user_style"] = DEFAULT_STYLE
    await state.set_data(data)

    start_message = (
        "Hello! I am a Google Gemini bot.\n\n"
        "I can remember our conversations and work with images.\n\n"
        "Here are my commands:\n"
        "/new - Start a new chat dialog\n"
        "/chats - View and manage your past dialogs\n"
        "/model - Choose your AI model\n"
        "/style - Change my communication style\n"
        "/help - Show a list of all commands\n\n"
        "Just send me a message or a photo to begin a new chat."
    )
    await message.answer(start_message)

@router.message(Command("help"))
async def help_command(message: types.Message):
    """Handler for /help command. Shows command list."""
    help_text = (
        "Here are the commands I understand:\n\n"
        "/start - Restart the bot and see the welcome message.\n"
        "/new - Start a new, fresh chat dialog.\n"
        "/chats - View, continue, or delete your past conversations.\n"
        "/model - Choose the AI model (e.g., Flash, Pro) you want to use.\n"
        "/style - Change my communication style (e.g., Formal, Explanatory).\n"
        "/help - Show this help message."
    )
    await message.answer(help_text)

@router.message(Command("new"))
async def new_chat(message: types.Message, state: FSMContext):
    """Handler for /new command. Sets active chat to None."""
    await state.update_data(active_chat_id=None)
    await message.answer("Started a new chat. Your next message will create it.")

@router.message(Command("chats"))
async def chats_command(message: types.Message, state: FSMContext):
    """Handler for /chats command. Shows list of saved chats."""
    data = await state.get_data()
    chats = data.get("chats", {})
    
    if not chats:
        await message.answer("You have no saved chats.")
        return

    buttons = []
    sorted_chats = sorted(chats.items(), key=lambda item: item[1].get("title", ""))
    
    for chat_id, chat_data in sorted_chats:
        title = chat_data.get("title", f"Chat {chat_id[:8]}")
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"chat:select:{chat_id}")])
    
    if not buttons:
        await message.answer("You have no saved chats.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Select a chat:", reply_markup=keyboard)

@router.message(Command("model"))
async def model_command(message: types.Message, state: FSMContext):
    """Shows the model selection menu."""
    data = await state.get_data()
    current_model = data.get("user_model", DEFAULT_MODEL)
    
    buttons = []
    for model_name in AVAILABLE_MODELS:
        text = model_name
        if model_name == current_model:
            text = f"✅ {model_name}"
        
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"model:select:{model_name}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"Current model: {current_model}\n\nSelect a new model:", reply_markup=keyboard)

@router.message(Command("style"))
async def style_command(message: types.Message, state: FSMContext):
    """Shows the style selection menu."""
    data = await state.get_data()
    current_style = data.get("user_style", DEFAULT_STYLE)
    
    buttons = []
    for style_key, prompt in SYSTEM_PROMPTS.items():
        text = style_key.capitalize()
        if style_key == current_style:
            text = f"✅ {text}"
        
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"style:select:{style_key}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"Current style: {current_style.capitalize()}\n\nSelect a new style:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("model:select:"))
async def cb_select_model(callback: CallbackQuery, state: FSMContext):
    """Sets the user's chosen model."""
    model_name = callback.data.split(":")[-1]
    
    if model_name not in AVAILABLE_MODELS:
        await callback.answer("Error: Unknown model.", show_alert=True)
        return

    await state.update_data(user_model=model_name)
    await callback.answer(f"Model set to {model_name}")

    buttons = []
    for m in AVAILABLE_MODELS:
        text = m
        if m == model_name:
            text = f"✅ {m}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"model:select:{m}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"Current model: {model_name}\n\nSelect a new model:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("style:select:"))
async def cb_select_style(callback: CallbackQuery, state: FSMContext):
    """Sets the user's chosen style."""
    style_key = callback.data.split(":")[-1]
    
    if style_key not in SYSTEM_PROMPTS:
        await callback.answer("Error: Unknown style.", show_alert=True)
        return

    await state.update_data(user_style=style_key)
    await callback.answer(f"Style set to {style_key.capitalize()}")

    buttons = []
    for s_key in SYSTEM_PROMPTS.keys():
        text = s_key.capitalize()
        if s_key == style_key:
            text = f"✅ {text}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"style:select:{s_key}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"Current style: {style_key.capitalize()}\n\nSelect a new style:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("chat:select:"))
async def cb_select_chat(callback: CallbackQuery, state: FSMContext):
    """Shows the history of a selected chat and management options."""
    chat_id = callback.data.split(":")[-1]
    data = await state.get_data()
    chat = data.get("chats", {}).get(chat_id)

    if not chat:
        await callback.answer("Chat not found.")
        await callback.message.delete()
        return

    await callback.message.delete()

    full_dialog = f"History for: {chat.get('title', 'Chat')}\n\n"
    history = chat.get("history", [])
    
    if not history:
        full_dialog += "(This chat is empty)"
    else:
        for msg in history:
            role = "You" if msg['role'] == 'user' else "Bot"
            text = " ".join(msg.get('parts', []))
            full_dialog += f"{role}: {text}\n\n"
    
    if len(full_dialog) > 4000:
        await callback.message.answer(full_dialog[:4000] + "\n\n... (message truncated)")
    else:
        await callback.message.answer(full_dialog)

    buttons = [
        [InlineKeyboardButton(text="Continue this Chat", callback_data=f"chat:continue:{chat_id}")],
        [InlineKeyboardButton(text="Delete this Chat", callback_data=f"chat:delete:{chat_id}")],
        [InlineKeyboardButton(text="Back to List", callback_data=f"chat:back_list")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("What would you like to do?", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("chat:continue:"))
async def cb_continue_chat(callback: CallbackQuery, state: FSMContext):
    """Sets the selected chat as active."""
    chat_id = callback.data.split(":")[-1]
    await state.update_data(active_chat_id=chat_id)
    await callback.message.edit_text("This is now your active chat. Send a message to continue.")
    await callback.answer()

@router.callback_query(F.data.startswith("chat:delete:"))
async def cb_delete_chat_prompt(callback: CallbackQuery):
    """Asks for confirmation before deleting a chat."""
    chat_id = callback.data.split(":")[-1]
    buttons = [
        [InlineKeyboardButton(text="YES, DELETE", callback_data=f"chat:delete_confirm:{chat_id}")],
        [InlineKeyboardButton(text="CANCEL", callback_data=f"chat:back_list")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Are you sure you want to delete this chat? This cannot be undone.", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("chat:delete_confirm:"))
async def cb_delete_chat_confirm(callback: CallbackQuery, state: FSMContext):
    """Deletes the chat from MongoDB."""
    chat_id = callback.data.split(":")[-1]
    data = await state.get_data()
    chats = data.get("chats", {})
    
    if chat_id in chats:
        del chats[chat_id]
    
    active_chat_id = data.get("active_chat_id")
    if active_chat_id == chat_id:
        active_chat_id = None
    
    user_model = data.get("user_model", DEFAULT_MODEL)
    user_style = data.get("user_style", DEFAULT_STYLE)
    await state.set_data({
        "chats": chats, 
        "active_chat_id": active_chat_id, 
        "user_model": user_model,
        "user_style": user_style
    })
    
    await callback.message.edit_text("Chat deleted.")
    await callback.answer()

@router.callback_query(F.data == "chat:back_list")
async def cb_back_to_list(callback: CallbackQuery, state: FSMContext):
    """Deletes the current message and shows the chat list again."""
    await callback.message.delete()
    await chats_command(callback.message, state)
    await callback.answer()


async def get_model_response(state: FSMContext, prompt_parts: list):
    """
    Gets response from Gemini.
    Manages chat creation and history persistence.
    """
    
    data = await state.get_data()
    chats = data.get("chats", {})
    active_chat_id = data.get("active_chat_id")
    
    model_name = data.get("user_model", DEFAULT_MODEL)
    style_key = data.get("user_style", DEFAULT_STYLE)
    
    system_instruction = SYSTEM_PROMPTS.get(style_key, SYSTEM_PROMPTS[DEFAULT_STYLE])
    
    user_model_to_save = model_name
    user_style_to_save = style_key

    if active_chat_id is None or active_chat_id not in chats:
        active_chat_id = str(uuid.uuid4())
        
        title_prompt = "Chat"
        for part in prompt_parts:
            if isinstance(part, str):
                title_prompt = part
                break
            elif isinstance(part, PIL.Image.Image):
                title_prompt = "Image Analysis"

        new_chat_title = title_prompt[:40] + ("..." if len(title_prompt) > 40 else "")
        
        chats[active_chat_id] = {
            "title": new_chat_title,
            "history": []
        }
    
    history = chats[active_chat_id]["history"]
    
    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_instruction
    )
    
    chat = model.start_chat(history=history)
    response = await chat.send_message_async(prompt_parts)
    
    serializable_history = []
    for content in chat.history:
        parts_list = []
        for part in content.parts:
            if part.text:
                parts_list.append(part.text)
        
        serializable_history.append({
            "role": content.role,
            "parts": parts_list
        })
    
    chats[active_chat_id]["history"] = serializable_history
    
    await state.set_data({
        "chats": chats,
        "active_chat_id": active_chat_id,
        "user_model": user_model_to_save,
        "user_style": user_style_to_save
    })
    
    return response.text


async def send_typing_periodically(chat_id: int):
    """Sends 'typing' action periodically."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error in typing task: {e}")


@router.message(F.text & ~F.text.startswith('/'))
async def handle_text(message: types.Message, state: FSMContext):
    """Handles all text messages."""
    
    typing_task = asyncio.create_task(
        send_typing_periodically(message.chat.id)
    )
    
    try:
        response_text = await get_model_response(state, [message.text])
        await message.answer(response_text)
        
    except Exception as e:
        print(f"Error processing text request: {e}")
        await message.answer("Sorry, an error occurred. Try starting a /new chat or changing your /model.")
    finally:
        typing_task.cancel()

@router.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    """Handles all photo messages."""
    
    typing_task = asyncio.create_task(
        send_typing_periodically(message.chat.id)
    )
    
    try:
        image_buffer = io.BytesIO()
        file = await bot.get_file(message.photo[-1].file_id)
        await bot.download_file(file.file_path, image_buffer)
        
        image = PIL.Image.open(image_buffer)
        prompt_text = message.caption or "What do you see in this image?"
        
        response_text = await get_model_response(state, [prompt_text, image])
        await message.answer(response_text)

    except Exception as e:
        print(f"Error processing photo request: {e}")
        await message.answer("Sorry, an error occurred processing the image. Try starting a /new chat or changing your /model.")
    finally:
        typing_task.cancel()


async def main():
    global bot 

    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URL)
    
    storage = MongoStorage(client=client, db_name=MONGO_DB_NAME)

    bot = Bot(token=BOT_TOKEN, parse_mode=None)
    dp = Dispatcher(storage=storage)
    
    dp.include_router(router)

    print("✅ Gemini bot is running (with MongoDB storage)...")
    
    try:
        await dp.start_polling(bot)
    finally:
        client.close()
        print("ℹ️ Bot stopped and MongoDB connection closed.")


if __name__ == "__main__":
    asyncio.run(main())