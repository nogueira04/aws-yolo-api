import json
import boto3
import os
import traceback
from decimal import Decimal 

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'yolo-jobs')
table = dynamodb.Table(TABLE_NAME)

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            if obj % 1 == 0:
                return int(obj)
            else:
                return float(obj)
        return super(DecimalEncoder, self).default(obj)

def handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    try:
        job_id = event.get('pathParameters', {}).get('job_id')

        if not job_id:
            print("[ERROR] Missing job_id path parameter")
            return {
                'statusCode': 400,
                'headers': { 'Content-Type': 'application/json' },
                'body': json.dumps({'error': 'Missing job_id path parameter'})
            }

        print(f"Fetching results for job_id: {job_id}")
        response = table.get_item(
            Key={'job_id': job_id},
        )

        item = response.get('Item')

        if not item:
            print(f"Job not found: {job_id}")
            return {
                'statusCode': 404,
                'headers': { 'Content-Type': 'application/json' },
                'body': json.dumps({'message': 'Job not found'})
            }
        else:
            print(f"Found job item.")
            return {
                'statusCode': 200,
                'headers': { 'Content-Type': 'application/json' },
                'body': json.dumps(item, cls=DecimalEncoder, indent=2) 
            }

    except Exception as e:
        print(f"[ERROR] Error in results_handler: {str(e)}")
        traceback.print_exc()
        return {
            'statusCode': 500,
            'headers': { 'Content-Type': 'application/json' },
            'body': json.dumps({'error': 'Failed to retrieve job results'})
        }
