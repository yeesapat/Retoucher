import discord
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import io
import os
import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# --- ตั้งค่า Discord Bot ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# --- ตั้งค่า Google Drive API ---
CREDENTIALS_FILE = '/Users/yeesmac/Downloads/Cred Retoucher JSON.json'
SCOPES = ['https://www.googleapis.com/auth/drive.file']
UPLOAD_FOLDER_ID = None

# --- Image Processing with OpenCV ---
def pil_to_cv2(image):
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

def cv2_to_pil(image):
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

def apply_color_tone_cv2(image, tone):
    if tone == "warm":
        image[:, :, 2] = cv2.add(image[:, :, 2], 30)
    elif tone == "cool":
        image[:, :, 0] = cv2.add(image[:, :, 0], 30)
    elif tone == "vintage":
        image = cv2.applyColorMap(image, cv2.COLORMAP_AUTUMN)
    return image

def smooth_image_cv2(image):
    return cv2.GaussianBlur(image, (5, 5), 0)

def denoise_image_cv2(image):
    return cv2.fastNlMeansDenoisingColored(image, None, 10, 10, 7, 21)

def automatic_color_correction(image):
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    cl = clahe.apply(l)
    limg = cv2.merge((cl,a,b))
    return cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)

def add_watermark_cv2(image, watermark_path, position="bottom-right"):
    watermark = cv2.imread(watermark_path, cv2.IMREAD_UNCHANGED)
    (wH, wW) = watermark.shape[:2]
    (h, w) = image.shape[:2]
    if position == "top-left":
        x, y = 10, 10
    elif position == "top-right":
        x, y = w - wW - 10, 10
    elif position == "bottom-left":
        x, y = 10, h - wH - 10
    else:
        x, y = w - wW - 10, h - wH - 10

    if watermark.shape[2] == 4:
        alpha_s = watermark[:, :, 3] / 255.0
        alpha_l = 1.0 - alpha_s
        for c in range(0, 3):
            image[y:y+wH, x:x+wW, c] = (alpha_s * watermark[:, :, c] +
                                       alpha_l * image[y:y+wH, x:x+wW, c])
    else:
        image[y:y+wH, x:x+wW] = watermark
    return image

# --- Retouch wrapper ---
def advanced_retouch(image_pil, keywords, watermark_path):
    img_cv2 = pil_to_cv2(image_pil)

    if "correct" in keywords:
        img_cv2 = automatic_color_correction(img_cv2)
    if "smooth" in keywords:
        img_cv2 = smooth_image_cv2(img_cv2)
    if "denoise" in keywords:
        img_cv2 = denoise_image_cv2(img_cv2)

    for tone in ["warm", "cool", "vintage"]:
        if tone in keywords:
            img_cv2 = apply_color_tone_cv2(img_cv2, tone)
            break

    positions = ["top-left", "top-right", "bottom-left", "bottom-right"]
    for pos in positions:
        if pos in keywords:
            img_cv2 = add_watermark_cv2(img_cv2, watermark_path, pos)
            break

    return cv2_to_pil(img_cv2)

# --- Example usage inside Discord event ---
# Inside your message handler, extract:
# keywords = message.content.lower().split()
# image = Image.open(BytesIO(downloaded_bytes))
# retouched = advanced_retouch(image, keywords, 'watermark.png')

# --- Original PIL-based fallback ---
def retouch_image(image):
    enhancer = ImageEnhance.Brightness(image)
    image = enhancer.enhance(1.15)
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(1.15)
    return image

def add_watermark(image, text="Living soon", color=(0, 255, 0, 255), font_size=20):
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    width, height = image.size
    draw.text((width - 120, height - 30), text, fill=color, font=font)
    return image
