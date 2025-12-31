import os
import base64
import json
import boto3
import requests
from email.parser import BytesParser
from email.policy import default
import boto3
from datetime import datetime


# DynamoDB rate limit table
RATE_LIMIT_TABLE = "DeepFakeIPRateLimit"
dynamodb = boto3.resource("dynamodb")
rate_table = dynamodb.Table(RATE_LIMIT_TABLE)

MAX_REQUESTS_PER_HOUR = 20


# Sightengine API base URL
SIGHTENGINE_URL = "https://api.sightengine.com/1.0/check.json"

# API credentials
API_USER = os.environ.get("SIGHTENGINE_API_USER")
API_SECRET = os.environ.get("SIGHTENGINE_API_SECRET")
AWS_REGION = os.environ.get("AWS_REGION")

# S3 config
S3_BUCKET = "deepfakedetection-bucket"
s3_client = boto3.client("s3")



def check_rate_limit(ip):
    """Check and update the IP rate limit."""
    now = datetime.now()
    period = now.strftime("%Y-%m-%d-%H")  # Hourly bucket

    response = rate_table.get_item(Key={"ip": ip, "period": period})
    count = response.get("Item", {}).get("count", 0)

    if count >= MAX_REQUESTS_PER_HOUR:
        return False  # Rate limit exceeded

    # Increment count
    rate_table.update_item(
        Key={"ip": ip, "period": period},
        UpdateExpression="SET #c = if_not_exists(#c, :start) + :inc",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":inc": 1, ":start": 0},
    )
    return True


def lambda_handler(event, context):
    ip = event["requestContext"]["http"]["sourceIp"]
    print("Received request from IP:", ip)

    # Rate limit check
    if not check_rate_limit(ip):
        return {
            "statusCode": 429,
            "body": json.dumps({"error": "Rate limit exceeded. Max 20 requests per hour."})
        }
    
    s3_key = None
    try:
        # Decode body
        body = event["body"]
        if event.get("isBase64Encoded", False):
            body = base64.b64decode(body)
        else:
            body = body.encode()

        # Get Content-Type header
        headers = event.get("headers", {})
        content_type = headers.get("Content-Type") or headers.get("content-type")
        if not content_type:
            return {"statusCode": 400, "body": json.dumps({"error": "Content-Type header missing"})}

        # Parse multipart form-data
        parser = BytesParser(policy=default)
        msg = parser.parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
        )

        image_bytes = None
        filename = None
        image_content_type = None

        # Extract file
        for part in msg.iter_parts():
            if part.get_param("name", header="content-disposition") == "media":
                filename = part.get_filename() or "uploaded_image"
                image_bytes = part.get_payload(decode=True)
                image_content_type = part.get_content_type()  # 🔹 KEY CHANGE

        if not image_bytes:
            return {"statusCode": 400, "body": json.dumps({"error": "No image file received under key 'media'"})}

        # Fallback if content-type is missing
        if not image_content_type or image_content_type == "application/octet-stream":
            image_content_type = "image/jpeg"

        # Upload to S3
        s3_key = f"uploads/{filename}"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=image_bytes,
            ContentType=image_content_type  # 🔹 dynamic
        )

        # Build region-specific S3 URL (no redirect)
        image_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

        # Call Sightengine
        params = {
            "models": "genai",
            "api_user": API_USER,
            "api_secret": API_SECRET,
            "url": image_url
        }

        response = None
        try:
            response = requests.get(SIGHTENGINE_URL, params=params, timeout=15)
        
        except requests.exceptions.RequestException as e:
            print("Sightengine request failed:", repr(e))
            raise

        return {
            "statusCode": response.status_code,
            "body": json.dumps(response.json())
        }

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    finally:
        # ⚠️ Optional: delete AFTER Sightengine fetch
        if s3_key:
            try:
                s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
            except Exception:
                pass
