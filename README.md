# YOLO Inference API on AWS

This repository contains the infrastructure code (CloudFormation) and Lambda function code for deploying a serverless, asynchronous API on AWS to perform object detection using a YOLO model. The API utilizes S3 presigned URLs for direct client uploads, triggering inference via S3 events, tracks job status in DynamoDB, and provides results via a RESTful endpoint secured with JWT authorization.

## Architecture Overview

The system employs the following AWS services and workflow:

1.  **Client:** Initiates the process by requesting a secure upload URL.
2.  **API Gateway (HTTP API):** Exposes two main endpoints:
    *   `GET /generate-upload-url`: Secured via JWT Authorizer. Requires `filename` and `contentType` query parameters. Returns a presigned S3 URL and a `job_id`.
    *   `GET /results/{job_id}`: Returns the status and results of a specific job.
3.  **Lambda (`GenerateUploadUrlHandler`):** 
    *   Triggered by `/generate-upload-url`.
    *   Generates a unique `job_id`.
    *   Creates an initial record in DynamoDB (`status: PENDING`).
    *   Generates a short-lived S3 presigned URL for `PUT` operations.
    *   Returns the `job_id` and `upload_url` to the client.
4.  **Client:** Uses the received `upload_url` to upload the image file directly to S3 using an HTTP `PUT` request.
5.  **S3 Bucket:**
    *   Receives the uploaded image in the `inputs/` prefix.
    *   An S3 Event Notification triggers the inference Lambda.
    *   Stores annotated output images in the `outputs/` prefix.
6.  **Lambda (`InferenceWorkerFunction`):** 
    *   Triggered by the S3 event.
    *   Parses the event to get the bucket name and object key.
    *   Extracts the `job_id` from the object key.
    *   Updates the job status to `PROCESSING` in DynamoDB.
    *   Downloads the input image from S3.
    *   Performs YOLO inference using the embedded model.
    *   Saves the annotated image locally.
    *   Extracts detection results (boxes, scores, labels) as JSON.
    *   Uploads the annotated image to the S3 `outputs/` prefix.
    *   Updates the DynamoDB record with `status: COMPLETED` (or `FAILED`), `results` (JSON), and `annotated_image_s3_path`.
7.  **Lambda (`ResultsHandlerFunction`):** 
    *   Triggered by `GET /results/{job_id}`.
    *   Queries DynamoDB using the `job_id`.
    *   Returns the full job record (status, results, links) as JSON.
8.  **DynamoDB:** A table stores job state (`job_id`, `status`, `input_s3_key`, `input_content_type`, `results`, `annotated_image_s3_path`, timestamps, `error_message`).
9.  **ECR (Elastic Container Registry):** Three private repositories host the Docker images for the three Lambda functions.
10. **CloudFormation:** The template (`yolo_api_stack.yaml`) defines and manages all the AWS infrastructure resources.
