import requests
import time
import argparse
import os
import json
import mimetypes

def get_upload_url(api_url: str, filename: str, content_type: str, access_token: str) -> tuple[str | None, str | None]:
    """Gets a presigned URL and job ID from the API."""

    generate_url_endpoint = f"{api_url.rstrip('/')}/generate-upload-url"
    params = {'filename': filename, 'contentType': content_type}
    headers = {
            'Authorization': f'Bearer {access_token}'
    }
    print(f"Requesting upload URL from {generate_url_endpoint} with params: {params}")
    try:
        response = requests.get(generate_url_endpoint, params=params, headers=headers, timeout=15)
        response.raise_for_status() 

        if response.status_code == 200:
            data = response.json()
            job_id = data.get('job_id')
            upload_url = data.get('upload_url')
            print(f"Successfully obtained upload URL for Job ID: {job_id}")
            return job_id, upload_url
        else:
            print(f"Error getting upload URL. Status: {response.status_code}, Body: {response.text}")
            return None, None

    except requests.exceptions.RequestException as e:
        print(f"An error occurred requesting the upload URL: {e}")
        if e.response is not None:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        return None, None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None, None

def upload_file_to_s3(upload_url: str, image_path: str, content_type: str) -> bool:
    """Uploads the file directly to S3 using the presigned URL."""

    print(f"Uploading '{os.path.basename(image_path)}' to S3 via presigned URL...")
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()

        headers = {'Content-Type': content_type}
        response = requests.put(upload_url, data=image_data, headers=headers, timeout=60) 

        response.raise_for_status() 

        if 200 <= response.status_code < 300:
            print("Upload to S3 successful.")
            return True
        else:
            print(f"S3 upload failed. Status: {response.status_code}, Body: {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"An error occurred during S3 upload: {e}")
        if e.response is not None:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred during S3 upload: {e}")
        return False


def poll_for_results(api_url: str, job_id: str, poll_interval: int = 5, max_attempts: int = 36) -> dict | None:
    """Polls the /results/{job_id} endpoint until completion or failure."""

    results_url = f"{api_url.rstrip('/')}/results/{job_id}"
    print(f"Polling for results for Job ID: {job_id} at {results_url}...")

    attempts = 0
    last_status = None
    while attempts < max_attempts:
        attempts += 1
        print(f"Attempt {attempts}/{max_attempts}...")
        try:
            response = requests.get(results_url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                status = data.get('status')
                print(f"  Status: {status}")
                last_status = status
                if status == 'COMPLETED':
                    print("Processing completed!")
                    return data
                elif status == 'FAILED':
                    print(f"Processing failed: {data.get('error_message', 'No details provided.')}")
                    return data
                elif status in ['PENDING', 'PROCESSING']:
                    pass
                else:
                    print(f"  Unknown status received: {status}. Stopping poll.")
                    return data
            elif response.status_code == 404:
                 print(f"  Job ID '{job_id}' not found (404). Still pending or invalid ID.")
                 last_status = 'NOT_FOUND'
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError as e:
             print(f"  An HTTP error occurred: {e}")
             print(f"  Response: {e.response.text}")
             last_status = 'HTTP_ERROR'
        except requests.exceptions.RequestException as e:
            print(f"  A network or request error occurred: {e}")
            last_status = 'REQUEST_ERROR'

        if last_status in ['PENDING', 'PROCESSING', 'NOT_FOUND', None]:
             time.sleep(poll_interval)
        else:
            break

    print(f"Polling timed out or stopped after {attempts} attempts.")
    try:
        final_response = requests.get(results_url, timeout=10)
        if final_response.status_code == 200: return final_response.json()
        else: return {"job_id": job_id, "status": "TIMEOUT_OR_ERROR", "error_message": f"Client polling finished. Last status code: {final_response.status_code}"}
    except requests.exceptions.RequestException as final_e:
        return {"job_id": job_id, "status": "TIMEOUT_OR_ERROR", "error_message": f"Client polling finished. Error on final fetch: {final_e}"}


def main():
    parser = argparse.ArgumentParser(description="Upload image to S3 via presigned URL and get YOLO results.")
    parser.add_argument("image_path", help="Path to the input image file.")
    parser.add_argument("--api_url", required=True, help="Base URL of the API (including stage, e.g., https://....execute-api.../prod).")
    parser.add_argument("--token", help="Access token for authorization.")
    parser.add_argument("--poll_interval", type=int, default=5, help="Polling interval in seconds (default: 5).")
    parser.add_argument("--max_attempts", type=int, default=36, help="Maximum polling attempts (default: 36 -> ~3 minutes).")

    args = parser.parse_args()

    if not os.path.exists(args.image_path):
        print(f"Error: Image file not found at '{args.image_path}'")
        return

    filename = os.path.basename(args.image_path)
    content_type, _ = mimetypes.guess_type(args.image_path)
    access_token = args.token or os.environ.get("YOLO_API_TOKEN")
    if content_type is None:
        content_type = 'application/octet-stream'
        print(f"Warning: Could not guess content type for '{filename}', using {content_type}")

    print("--- Step 1: Getting Upload URL ---")
    job_id, upload_url = get_upload_url(args.api_url, filename, content_type, access_token)

    if not job_id or not upload_url:
        print("Failed to get upload URL. Exiting.")
        return

    print("\n--- Step 2: Uploading to S3 ---")
    upload_successful = upload_file_to_s3(upload_url, args.image_path, content_type)

    if not upload_successful:
        print("Failed to upload file to S3. Exiting.")
        return

    print("\n--- Step 3: Polling for Results ---")
    final_result = poll_for_results(args.api_url, job_id, args.poll_interval, args.max_attempts)

    print("\n--- Final Result ---")
    if final_result:
        print(json.dumps(final_result, indent=2))
        status = final_result.get("status")
        if status == "COMPLETED":
             print("\nDetection successful!")
             annotated_path = final_result.get("annotated_image_s3_path")
             if annotated_path:
                  print(f"\nAnnotated image available at: {annotated_path}")
        elif status == "FAILED":
             print(f"\nDetection failed: {final_result.get('error_message', 'No details')}")
        elif status == "TIMEOUT_OR_ERROR":
             print(f"\nClient polling stopped: {final_result.get('error_message', 'Timeout or error occurred')}")
        else:
            print(f"\nDetection ended with status: {status}")
    else:
        print("Could not retrieve final results for the job after polling.")
    print("--------------------\n")

if __name__ == "__main__":
    main()
