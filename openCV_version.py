import discord
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import cv2
import numpy as np
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import io
import os
from dotenv import load_dotenv

load_dotenv()

# --- ตั้งค่า Discord Bot ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1366630727298318379

# --- ตั้งค่า Google Drive API ---
CREDENTIALS_FILE = '/Users/yeesmac/Downloads/CredRetoucher.json'
SCOPES = ['https://www.googleapis.com/auth/drive.file']
UPLOAD_FOLDER_ID = None  # Optional: Set to your base folder ID

# --- Retouch ---
# --- Retouch using OpenCV ---
def retouch_image(pil_image):
    # Convert PIL image to OpenCV format
    cv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)

    # Adjust brightness and contrast
    alpha = 1.2  # Contrast control (1.0-3.0)
    beta = 7     # Brightness control (0-100)
    cv_image = cv2.convertScaleAbs(cv_image, alpha=alpha, beta=beta)

    # Apply smoothing (Gaussian Blur)
    smoothed = cv2.GaussianBlur(cv_image, (3, 3), 0)

    # Convert back to PIL image
    processed_image = Image.fromarray(cv2.cvtColor(smoothed, cv2.COLOR_BGR2RGB))
    return processed_image


# --- watermark  ---
def add_watermark(image, watermark_path="/Users/yeesmac/Downloads/Water_Mark.png", position="top-right", margin=20):
    # Open watermark image with alpha
    watermark = Image.open(watermark_path).convert("RGBA")
    image = image.convert("RGBA")

    watermark = watermark.resize((300, 300))  
    img_w, img_h = image.size
    wm_w, wm_h = watermark.size

    # Position watermark
    if position == "top-right":
        x = img_w - wm_w - margin
        y = margin
    elif position == "bottom-right":
        x = img_w - wm_w - margin
        y = img_h - wm_h - margin
    elif position == "bottom-left":
        x = margin
        y = img_h - wm_h - margin
    elif position == "top-left":
        x = margin
        y = margin
    else:  # center
        x = (img_w - wm_w) // 2
        y = (img_h - wm_h) // 2

    # Paste with transparency
    image.paste(watermark, (x, y), watermark)
    return image.convert("RGB")


# --- upload Google Drive ---
def create_drive_folder(folder_name, parent_id=None):
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    try:
        service = build('drive', 'v3', credentials=creds)
        folder_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            folder_metadata['parents'] = [parent_id]

        folder = service.files().create(body=folder_metadata, fields='id, webViewLink').execute()

        service.permissions().create(
            fileId=folder['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()

        return folder['id'], folder['webViewLink']
    except HttpError as error:
        print(f'Error creating folder: {error}')
        return None, None

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
            fields='id'
        ).execute()

        # Make file public
        service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        return True
    
    except HttpError as error:
        print(f'An error occurred during upload: {error}')
        return False

# --- Discord Bot ---
class ImageProcessingClient(discord.Client):
    async def on_ready(self):
        print(f'✅ Logged in as {self.user}')

    async def on_message(self, message):
        if message.author == self.user:
            return

        if message.channel.id == CHANNEL_ID and message.attachments:
            folder_name = f"Processed_{message.id}"
            folder_id, folder_link = create_drive_folder(folder_name, parent_id=UPLOAD_FOLDER_ID)

            if not folder_id:
                await message.reply('❌ ไม่สามารถสร้างโฟลเดอร์บน Google Drive ได้')
                return

            success_count = 0
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    try:
                        image_bytes = await attachment.read()
                        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                        retouched_image = retouch_image(image)
                        watermarked_image = add_watermark(retouched_image)

                        output_buffer = io.BytesIO()
                        watermarked_image.save(output_buffer, format='PNG')
                        output_buffer.seek(0)

                        output_filename = f'processed_{attachment.filename.rsplit(".", 1)[0]}.png'
                        uploaded = upload_to_google_drive(
                            output_buffer.read(),
                            filename=output_filename,
                            folder_id=folder_id
                        )

                        if uploaded:
                            success_count += 1
                        else:
                            await message.reply(f'❌ ไม่สามารถอัปโหลดภาพ: {attachment.filename}')

                    except Exception as e:
                        print(f'Error processing {attachment.filename}: {e}')
                        await message.reply(f'⚠️ เกิดข้อผิดพลาดกับไฟล์ {attachment.filename}: {e}')

            if success_count > 0:
                await message.reply(f'✅ ประมวลผลและอัปโหลด {success_count} ภาพเรียบร้อยแล้ว: {folder_link}')
            else:
                await message.reply('❌ ไม่สามารถอัปโหลดภาพได้')


intents = discord.Intents.default()
intents.message_content = True
client = ImageProcessingClient(intents=intents)
client.run(DISCORD_TOKEN)

