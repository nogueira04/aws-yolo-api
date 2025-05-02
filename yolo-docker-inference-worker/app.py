import boto3
import os
import json
import traceback
import datetime
import mimetypes 
import urllib.parse
from decimal import Decimal
from ultralytics import YOLO

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'yolo-jobs')
INPUT_BUCKET = os.environ.get('INPUT_BUCKET', 'gabriel-yolo-bucket')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', 'gabriel-yolo-bucket') 
MODEL_FILENAME = os.environ.get('MODEL_FILENAME', 'yolov8n.pt')

table = dynamodb.Table(TABLE_NAME)
TMP_DIR = '/tmp/'


model_load_error = None
model = None
try:
    task_root = os.environ.get('LAMBDA_TASK_ROOT', '.')
    full_model_path = os.path.join(task_root, MODEL_FILENAME)
    print(f"Attempting to load model from: {full_model_path}")
    if not os.path.exists(full_model_path):
         print(f"[ERROR] Model file not found at expected path: {full_model_path}")
         model = YOLO(MODEL_FILENAME)
    else:
         model = YOLO(full_model_path)
    print("Model loaded successfully.")
except Exception as e:
    model_load_error = e
    print(f"FATAL: Failed to load model: {str(e)}")
    traceback.print_exc()

def get_results_from_ultralytics(results_obj):
    output = []
    if not results_obj or len(results_obj) == 0: return output
    boxes = results_obj[0].boxes
    names = results_obj[0].names
    for i in range(len(boxes.cls)):
        coords = boxes.xyxy[i].tolist()
        conf = boxes.conf[i].item()
        cls_id = int(boxes.cls[i].item())
        label = names.get(cls_id, f"class_{cls_id}")
        output.append({
            "box": [round(c) for c in coords],
            "score": round(conf, 4),
            "label": label
        })
    return output

def handler(event, context):
    if model_load_error:
         print("[HANDLER ERROR] Model failed to load during initialization.")
         raise model_load_error

    print(f"Received S3 event: {json.dumps(event)}")

    try:
        s3_record = event['Records'][0]['s3']
        bucket_name = s3_record['bucket']['name']
        object_key = urllib.parse.unquote_plus(s3_record['object']['key'], encoding='utf-8')
        print(f"Processing object s3://{bucket_name}/{object_key}")

        filename = os.path.basename(object_key)
        job_id, input_extension = os.path.splitext(filename)
        if not job_id or not input_extension: 
             print(f"[ERROR] Could not extract job_id and extension from key: {object_key}")
             raise ValueError("Invalid object key format")
        print(f"Extracted Job ID: {job_id}, Extension: {input_extension}")

        input_content_type = None
        try:
            db_response = table.get_item(Key={'job_id': job_id})
            item = db_response.get('Item')
            if item:
                input_content_type = item.get('input_content_type')
                print(f"Retrieved ContentType from DB: {input_content_type}")
            else:
                print(f"[WARNING] Job item {job_id} not found in DB when fetching content type.")
        except Exception as db_e:
            print(f"[WARNING] Error fetching content type from DB for {job_id}: {str(db_e)}")


        local_input_path = os.path.join(TMP_DIR, f"{job_id}_input{input_extension}")
        local_output_path = os.path.join(TMP_DIR, f"{job_id}_annotated{input_extension}")
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"

        print(f"Updating status to PROCESSING for job {job_id}")
        table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET #st = :val, #upd = :ts',
            ExpressionAttributeNames={'#st': 'status', '#upd': 'updated_at'},
            ExpressionAttributeValues={':val': 'PROCESSING', ':ts': timestamp }
        )

        print(f"Downloading s3://{bucket_name}/{object_key} to {local_input_path}")
        s3.download_file(bucket_name, object_key, local_input_path)
        print("Download complete.")

        print("Running inference...")
        results = model(local_input_path)
        print("Inference complete.")

        print(f"Saving annotated image to {local_output_path}...")
        results[0].save(filename=local_output_path)
        print("Saving complete.")

        print("Extracting results data...")
        inference_results_json = get_results_from_ultralytics(results)
        print(f"Extracted results: {json.dumps(inference_results_json)}")

        results_for_db = []
        for detection in inference_results_json:
            detection_for_db = detection.copy()
            score_value = detection_for_db.get('score')

            if isinstance(score_value, (float, int)):
                detection_for_db['score'] = Decimal(str(score_value))
            elif score_value is not None:
                try:
                    detection_for_db['score'] = Decimal(score_value)
                except Exception:
                    print(f"[WARNING] Could not convert score '{score_value}' to Decimal. Leaving as is.")

            results_for_db.append(detection_for_db)

        print(f"Results prepared for DB: {results_for_db}")

        output_key = f"outputs/{job_id}_annotated{input_extension}"
        annotated_image_s3_path = f"s3://{OUTPUT_BUCKET}/{output_key}"
        upload_content_type = input_content_type or mimetypes.guess_type(output_key)[0] or 'application/octet-stream'
        print(f"Uploading {local_output_path} to {annotated_image_s3_path}")
        s3.upload_file(local_output_path, OUTPUT_BUCKET, output_key, ExtraArgs={'ContentType': upload_content_type})
        print("Upload complete.")

        print(f"Updating status to COMPLETED for job {job_id}")
        update_timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET #st = :stat, #res = :res, #ann_path = :ann_path, #upd = :ts',
            ExpressionAttributeNames={
                '#st': 'status', '#res': 'results',
                '#ann_path': 'annotated_image_s3_path', '#upd': 'updated_at'
            },
            ExpressionAttributeValues={
                ':stat': 'COMPLETED',
                ':res': results_for_db,
                ':ann_path': annotated_image_s3_path,
                ':ts': update_timestamp
            }
        )
        print(f"Job {job_id} completed successfully.")
        return {"status": "COMPLETED", "job_id": job_id}

    except Exception as e:
        print(f"[HANDLER ERROR] Error processing S3 event for key {object_key}: {str(e)}")
        traceback.print_exc()
        if 'job_id' in locals() and job_id:
            error_timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            try:
                table.update_item(
                    Key={'job_id': job_id},
                    UpdateExpression='SET #st = :stat, #err = :err_msg, #upd = :ts',
                    ExpressionAttributeNames={'#st': 'status', '#err': 'error_message', '#upd': 'updated_at'},
                    ExpressionAttributeValues={':stat': 'FAILED', ':err_msg': str(e),':ts': error_timestamp}
                )
            except Exception as dbe:
                print(f"Failed to update DynamoDB status to FAILED after S3 event error: {str(dbe)}")

    finally:
        print("Cleaning up /tmp directory...")
        files_to_clean = [local_input_path, local_output_path]
        for f_path in files_to_clean:
             if os.path.exists(f_path):
                 try:
                     os.remove(f_path)
                     print(f"Removed temporary file: {f_path}")
                 except OSError as ose:
                     print(f"Error removing temporary file {f_path}: {ose}")
