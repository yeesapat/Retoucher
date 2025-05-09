import discord
from discord import ui, ButtonStyle, File
from discord.ext import commands
import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import cv2
import numpy as np
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import io
import os
import tempfile
from dotenv import load_dotenv
import re
import sys

load_dotenv()

# --- Bot Configuration ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))  # Replace with your channel ID
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE")  # Path to your Google credentials JSON
UPLOAD_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")  # Optional: Set to your base folder ID
WATERMARK_PATH = os.getenv("WATERMARK_PATH", "Water_Mark.png")  # Path to your watermark file

# Check if credentials file exists
if CREDENTIALS_FILE is None:
    print("WARNING: GOOGLE_CREDENTIALS_FILE environment variable is not set.")
    print("Google Drive upload functionality will be disabled.")
elif not os.path.exists(CREDENTIALS_FILE):
    print(f"WARNING: Google credentials file not found at {CREDENTIALS_FILE}")
    print("Google Drive upload functionality will be disabled.")

# --- Google Drive API Configuration ---
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# --- Global Storage for Processing Sessions ---
active_sessions = {}

class ImageQCSession:
    def __init__(self, message_id, supply_id, original_images, user_id):
        self.message_id = message_id
        self.supply_id = supply_id
        self.original_images = original_images  # List of original image data
        self.processed_images = []  # Will store processed images
        self.current_index = 0
        self.user_id = user_id
        self.qc_status = []  # Will be populated with True/False/None for each image (Pass/Not Pass/Pending)
        self.folder_id = None
        self.folder_link = None
    
    def is_complete(self):
        # Check if all images have been reviewed
        return all(status is not None for status in self.qc_status)
    
    def all_passed(self):
        # Check if all images passed QC
        return all(status is True for status in self.qc_status)

# --- Image Processing Functions ---
def retouch_image(pil_image):
    # Convert PIL image to OpenCV format
    cv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)

    # Adjust brightness and contrast
    alpha = 1.375 # Contrast control (1.0-3.0)
    beta = 9     # Brightness control (0-100)
    cv_image = cv2.convertScaleAbs(cv_image, alpha=alpha, beta=beta)

    # Apply smoothing (Gaussian Blur)
    smoothed = cv2.GaussianBlur(cv_image, (3, 3), 0)

    # Convert back to PIL image
    processed_image = Image.fromarray(cv2.cvtColor(smoothed, cv2.COLOR_BGR2RGB))
    return processed_image

def add_watermark(image, watermark_path=WATERMARK_PATH, position="top-right", margin=5, opacity=0.2):
    try:
        # Check if watermark file exists
        if not os.path.exists(watermark_path):
            print(f"Warning: Watermark file not found at {watermark_path}")
            return image.convert("RGB") if image.mode != "RGB" else image
        
        # Open watermark image with alpha
        watermark = Image.open(watermark_path).convert("RGBA")
        image = image.convert("RGBA")
        img_w, img_h = image.size
        wm_w, wm_h = watermark.size
        scale = img_w / 3755
        watermark = watermark.resize((int(scale * img_w), int(scale * img_h)))
        wm_w, wm_h = watermark.size
        alpha = watermark.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        watermark.putalpha(alpha)
        

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
    except Exception as e:
        print(f"Error applying watermark: {e}")
        return image.convert("RGB") if image.mode != "RGB" else image

# --- Google Drive Functions ---
def is_gdrive_enabled():
    """Check if Google Drive functionality is properly configured"""
    return CREDENTIALS_FILE is not None and os.path.exists(CREDENTIALS_FILE)

def create_drive_folder(folder_name, parent_id=None):
    if not is_gdrive_enabled():
        print("Cannot create Google Drive folder: credentials not configured")
        return None, None
    
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
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
    except Exception as error:
        print(f'Error creating folder: {error}')
        return None, None

def upload_to_google_drive(image_data, filename='processed_image.png', folder_id=None):
    if not is_gdrive_enabled():
        print("Cannot upload to Google Drive: credentials not configured")
        return None, None
    
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
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

        # Make file public
        service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        return file['id'], file['webViewLink']
    
    except Exception as error:
        print(f'An error occurred during upload: {error}')
        return None, None

# --- UI Components ---
class QCButtons(ui.View):
    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
    
    @ui.button(label="◀ Previous", style=ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        if self.session.current_index > 0:
            self.session.current_index -= 1
            await update_qc_message(interaction, self.session)
        else:
            await interaction.response.send_message("Already at the first image.", ephemeral=True)
    
    @ui.button(label="Next ▶", style=ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        if self.session.current_index < len(self.session.processed_images) - 1:
            self.session.current_index += 1
            await update_qc_message(interaction, self.session)
        else:
            await interaction.response.send_message("Already at the last image.", ephemeral=True)
    
    @ui.button(label="❌ Cancel", style=ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        # Cancel the entire process
        await interaction.response.send_message("QC process cancelled.", ephemeral=False)
        
        # Delete session
        if self.session.message_id in active_sessions:
            del active_sessions[self.session.message_id]
            
        # Clean up the message
        await interaction.message.delete()
    
    @ui.button(label="❌ Not Pass", style=ButtonStyle.danger)
    async def not_pass_button(self, interaction: discord.Interaction, button: ui.Button):
        # Mark current image as not passed
        self.session.qc_status[self.session.current_index] = False
        
        await interaction.response.send_message(
            f"Image {self.session.current_index + 1} marked as NOT PASSED.", 
            ephemeral=False
        )
        
        # Move to next image if available
        if self.session.current_index < len(self.session.processed_images) - 1:
            self.session.current_index += 1
            await update_qc_message(interaction, self.session)
        else:
            # Check if all images have been reviewed
            if self.session.is_complete():
                await finalize_qc_process(interaction, self.session)
    
    @ui.button(label="✅ Pass QC", style=ButtonStyle.success)
    async def pass_button(self, interaction: discord.Interaction, button: ui.Button):
        # Mark current image as passed
        self.session.qc_status[self.session.current_index] = True
        
        await interaction.response.send_message(
            f"Image {self.session.current_index + 1} marked as PASSED.", 
            ephemeral=False
        )
        
        # Move to next image if available
        if self.session.current_index < len(self.session.processed_images) - 1:
            self.session.current_index += 1
            await update_qc_message(interaction, self.session)
        else:
            # Check if all images have been reviewed
            if self.session.is_complete():
                await finalize_qc_process(interaction, self.session)

# --- Helper Functions ---
async def update_qc_message(interaction, session):
    # Create a temporary file to send the current image
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        current_image = session.processed_images[session.current_index]
        current_image.save(temp_file, format="PNG")
    
    # Build status message
    status_markers = []
    for i, status in enumerate(session.qc_status):
        if i == session.current_index:
            marker = "🔍"  # Current image
        elif status is True:
            marker = "✅"  # Passed
        elif status is False:
            marker = "❌"  # Not passed
        else:
            marker = "⬜"  # Pending
        status_markers.append(marker)
    
    status_line = " ".join(status_markers)
    file = File(temp_file.name, filename="preview.png")
    
    embed = discord.Embed(
        title=f"QC Review - Supply ID: {session.supply_id}",
        description=f"Image {session.current_index + 1} of {len(session.processed_images)}\n{status_line}",
        color=0x3498db
    )
    
    embed.set_image(url="attachment://preview.png")
    
    try:
        await interaction.response.edit_message(embed=embed, attachments=[file], view=QCButtons(session))
    except discord.errors.InteractionResponded:
        await interaction.message.edit(embed=embed, attachments=[file], view=QCButtons(session))
    
    # Delete the temporary file after sending
    os.unlink(temp_file.name)

async def finalize_qc_process(interaction, session):
    approved_images = []
    passed_count = sum(1 for status in session.qc_status if status is True)
    failed_count = sum(1 for status in session.qc_status if status is False)
    
    # Upload passed images to Google Drive
    if session.all_passed():
        # Upload all images that passed QC
        for i, (img, status) in enumerate(zip(session.processed_images, session.qc_status)):
            if status:  # True means passed
                # Convert to bytes
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                img_byte_arr.seek(0)
                
                # Add to approved images list
                approved_images.append((img, f"processed_image_{i+1}.png"))
        
        # Check if Google Drive functionality is available
        if is_gdrive_enabled():
            # Create folder and upload images
            folder_name = f"Approved_{session.supply_id}"
            folder_id, folder_link = create_drive_folder(folder_name, parent_id=UPLOAD_FOLDER_ID)
            
            if folder_id:
                # Upload all approved images
                upload_results = []
                for img, filename in approved_images:
                    # Convert to bytes
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    img_bytes = img_byte_arr.getvalue()
                    
                    # Upload
                    file_id, file_link = upload_to_google_drive(img_bytes, filename=filename, folder_id=folder_id)
                    if file_id:
                        upload_results.append((filename, file_link))
                
                # Final success message
                if upload_results:
                    await interaction.channel.send(
                        f"✅ QC Complete for Supply ID: {session.supply_id}\n"
                        f"All {len(upload_results)} images passed QC and were uploaded to Google Drive.\n"
                        f"📁 Folder Link: {folder_link}"
                    )
                else:
                    await interaction.channel.send(
                        f"⚠️ QC Complete for Supply ID: {session.supply_id}, but no images were uploaded successfully."
                    )
            else:
                await interaction.channel.send(
                    f"❌ QC Complete for Supply ID: {session.supply_id}, but failed to create Google Drive folder."
                )
        else:
            # Google Drive functionality not available - save locally
            # Create a directory to save passed images
            local_dir = f"approved_images_{session.supply_id}"
            os.makedirs(local_dir, exist_ok=True)
            
            # Save all approved images locally
            saved_count = 0
            for i, (img, filename) in enumerate(approved_images):
                try:
                    filepath = os.path.join(local_dir, filename)
                    img.save(filepath, format='PNG')
                    saved_count += 1
                except Exception as e:
                    print(f"Error saving image {filename}: {e}")
            
            await interaction.channel.send(
                f"✅ QC Complete for Supply ID: {session.supply_id}\n"
                f"All {passed_count} images passed QC.\n"
                f"⚠️ Google Drive is not configured, so {saved_count} images were saved locally in folder: {local_dir}"
            )
    else:
        # Some images failed QC
        await interaction.channel.send(
            f"⚠️ QC Complete for Supply ID: {session.supply_id}\n"
            f"Results: {passed_count} passed, {failed_count} failed.\n"
            f"Please submit corrected images for the failed ones."
        )
    
    # Clean up the session
    if session.message_id in active_sessions:
        del active_sessions[session.message_id]
    
    # Clean up the QC message
    try:
        await interaction.message.delete()
    except Exception as e:
        print(f"Error deleting message: {e}")
        pass

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ Bot is ready: {bot.user}')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="for images to process"))

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return
    
    # Process commands first
    await bot.process_commands(message)
    
    # Check if message has attachments and is in the correct channel
    if (message.channel.id == CHANNEL_ID or CHANNEL_ID == 0) and message.attachments:
        # Extract Supply ID from message content using regex
        supply_id_match = re.search(r'(?i)supply\s*id:?\s*(\w+)', message.content)
        supply_id = supply_id_match.group(1) if supply_id_match else f"Unknown_{message.id}"
        
        # Filter only image attachments
        image_attachments = [
            attachment for attachment in message.attachments 
            if attachment.content_type and attachment.content_type.startswith('image/')
        ]
        
        if not image_attachments:
            await message.reply("❌ No valid image attachments found.")
            return
        
        # Create a processing status message
        status_message = await message.reply(f"⏳ Processing {len(image_attachments)} images for Supply ID: {supply_id}...")
        
        # Prepare the session
        session = ImageQCSession(
            message_id=status_message.id,
            supply_id=supply_id,
            original_images=[],
            user_id=message.author.id
        )
        
        try:
            # Process each image
            for attachment in image_attachments:
                try:
                    # Download the image
                    image_bytes = await attachment.read()
                    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                    
                    # Store original image
                    session.original_images.append(image)
                    
                    # Process the image
                    retouched_image = retouch_image(image)
                    watermarked_image = add_watermark(retouched_image)
                    
                    # Store processed image
                    session.processed_images.append(watermarked_image)
                    
                    # Initialize QC status as None (pending)
                    session.qc_status.append(None)
                except Exception as e:
                    print(f"Error processing attachment {attachment.filename}: {e}")
            
            # If no images were processed successfully
            if not session.processed_images:
                await status_message.edit(content="❌ Failed to process any of the attached images.")
                return
                
            # Save session
            active_sessions[status_message.id] = session
            
            # Create a temporary file to send the first processed image
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                session.processed_images[0].save(temp_file, format="PNG")
            
            # Create an embed for the QC interface
            file = File(temp_file.name, filename="preview.png")
            
            embed = discord.Embed(
                title=f"QC Review - Supply ID: {supply_id}",
                description=f"Image 1 of {len(session.processed_images)}\n" + 
                            "🔍 " + "⬜ " * (len(session.processed_images) - 1),
                color=0x3498db
            )
            
            embed.set_image(url="attachment://preview.png")
            
            # Replace the status message with the QC interface
            try:
                await status_message.delete()
            except Exception as e:
                print(f"Error deleting status message: {e}")
                
            try:
                qc_message = await message.reply(
                    embed=embed, 
                    file=file,
                    view=QCButtons(session)
                )
                
                # Update the message ID in the session
                session.message_id = qc_message.id
                active_sessions[qc_message.id] = session
            except Exception as e:
                print(f"Error sending QC message: {e}")
                await message.reply(f"❌ Error creating QC interface: {str(e)}")
            
            # Delete the temporary file
            try:
                os.unlink(temp_file.name)
            except Exception as e:
                print(f"Error deleting temporary file: {e}")
            
        except Exception as e:
            await status_message.edit(content=f"❌ Error processing images: {str(e)}")
            print(f"Error processing images: {e}")

# --- Run the bot ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)