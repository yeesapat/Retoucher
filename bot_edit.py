import discord
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import io
import os
import logging
from dotenv import load_dotenv

# --- Load Environment Variables ---
load_dotenv()

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)

# --- Discord Bot Configuration ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # Convert to int

# --- Google Drive API Configuration ---
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")
SCOPES = ['https://www.googleapis.com/auth/drive.file']
UPLOAD_FOLDER_ID = os.getenv("UPLOAD_FOLDER_ID") or None

# --- Image Retouching Function ---
def retouch_image(image):
    enhancer = ImageEnhance.Brightness(image)
    image = enhancer.enhance(1.15)
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(1.15)
    return image

# --- Add Watermark Function ---
def add_watermark(image, text="Living soon", color=(0, 255, 0, 128)):
    image = image.convert("RGBA")
    draw = ImageDraw.Draw(image)

    width, height = image.size
    font_size = min(width, height) // 10

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    position = (width - text_width - 10, height - text_height - 10)

    draw.text(position, text, fill=color, font=font)
    return image.convert("RGB")

# --- Upload to Google Drive ---
def upload_to_google_drive(image_data, filename='processed_image.png', folder_id=None):
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    try:
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {'name': filename}
        if folder_id:
            file_metadata['parents'] = [folder_id]

        media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype='image/png')

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        file_id = file.get('id')
        permission = {
            'role': 'reader',
            'type': 'anyone',
        }
        service.permissions().create(fileId=file_id, body=permission).execute()

        return file.get('webViewLink')
    except HttpError as error:
        logging.error(f'An error occurred: {error}')
        return None

# --- Discord Client ---
class ImageProcessingClient(discord.Client):
    async def on_ready(self):
        logging.info(f'Logged in as {self.user}')

    async def on_message(self, message):
        if message.author == self.user:
            return

        if message.channel.id == CHANNEL_ID and message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    logging.info(f'Processing image: {attachment.filename}')
                    try:
                        image_bytes = await attachment.read()
                        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

                        retouched_image = retouch_image(image)
                        watermarked_image = add_watermark(retouched_image)

                        output_buffer = io.BytesIO()
                        watermarked_image.save(output_buffer, format='PNG')
                        output_buffer.seek(0)

                        drive_link = upload_to_google_drive(
                            output_buffer.read(),
                            filename=f'processed_{attachment.filename}.png',
                            folder_id=UPLOAD_FOLDER_ID
                        )

                        if drive_link:
                            await message.reply(f'ภาพถูกประมวลผลและบันทึกลง Google Drive แล้ว: {drive_link}')
                        else:
                            await message.reply('เกิดข้อผิดพลาดในการอัปโหลดไปยัง Google Drive')

                    except Exception as e:
                        logging.error(f'Error processing {attachment.filename}: {e}')
                        await message.reply(f'เกิดข้อผิดพลาดในการประมวลผลภาพ: {e}')

# --- Start Bot ---
intents = discord.Intents.default()
intents.message_content = True
client = ImageProcessingClient(intents=intents)
client.run(DISCORD_TOKEN)
