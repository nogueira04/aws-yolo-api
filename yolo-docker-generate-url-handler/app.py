import json
import boto3
import uuid
import os
import datetime
import traceback
import mimetypes

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'yolo-jobs')
BUCKET_NAME = os.environ.get('BUCKET_NAME', 'gabriel-yolo-bucket')
URL_EXPIRATION_SECONDS = int(os.environ.get('URL_EXPIRATION_SECONDS', 300)) 
table = dynamodb.Table(TABLE_NAME)

def handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    try:
        query_params = event.get('queryStringParameters', {})
        if not query_params:
             if event.get('body'):
                 try:
                     query_params = json.loads(event['body'])
                 except json.JSONDecodeError:
                     print("[WARN] Request body is not valid JSON.")
             if not query_params: 
                 print("[ERROR] Missing required query parameters (filename or contentType).")
                 return {'statusCode': 400, 'body': json.dumps({'error': 'Query parameters filename or contentType are required.'})}
        filename = query_params.get('filename')
        content_type = query_params.get('contentType')

        if not content_type:
            if filename:
                 content_type, _ = mimetypes.guess_type(filename)
                 if not content_type:
                     content_type = 'application/octet-stream'
                     print(f"[WARN] Could not guess contentType from filename '{filename}', using default: {content_type}")
                 else:
                      print(f"Guessed contentType '{content_type}' from filename '{filename}'")
            else:
                  print("[ERROR] Either filename or contentType query parameter is required.")
                  return {'statusCode': 400, 'body': json.dumps({'error': 'Query parameter filename or contentType is required.'})}
        else:
             print(f"Using provided contentType: {content_type}")


        job_id = str(uuid.uuid4())
        file_extension = mimetypes.guess_extension(content_type.split(';')[0].strip()) or '.bin'
        object_key = f"inputs/{job_id}{file_extension}"
        s3_path = f"s3://{BUCKET_NAME}/{object_key}"
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"

        print(f"Creating initial DynamoDB record for job_id: {job_id}")
        table.put_item(
            Item={
                'job_id': job_id,
                'status': 'PENDING', 
                'input_s3_key': object_key,
                'input_content_type': content_type,
                'created_at': timestamp,
                'updated_at': timestamp
            }
        )
        print("DynamoDB record created.")

        print(f"Generating presigned PUT URL for BUCKET={BUCKET_NAME}, KEY={object_key}, ContentType={content_type}")
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': object_key,
                'ContentType': content_type 
            },
            ExpiresIn=URL_EXPIRATION_SECONDS,
            HttpMethod='PUT'
        )
        print("Presigned URL generated.")

        response_body = {
            'job_id': job_id,
            'upload_url': presigned_url,
            's3_key': object_key, 
            'expires_in': URL_EXPIRATION_SECONDS
        }

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*' 
            },
            'body': json.dumps(response_body)
        }

    except Exception as e:
        print(f"[ERROR] Error generating upload URL: {str(e)}")
        traceback.print_exc()
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'Failed to generate upload URL'})
        }
