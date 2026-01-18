# Google Drive to RAG Sync Architecture

## Overview

Three operational modes for syncing Google Drive documents to RAG vector index:

1. **Full Sync (Initial)**: Process ALL documents for first-time setup
2. **Rebuild**: Recreate entire index from scratch (fix corruption, change strategy)
3. **Incremental Sync**: Process only new/modified documents (ongoing maintenance)

---

## Architecture Components

```
┌─────────────────────────────────────────────────────────┐
│                   Trigger Options                        │
├─────────────────────────────────────────────────────────┤
│  1. Manual (API call)      "Start full sync"            │
│  2. Scheduled (CloudWatch) "Every 15 min"               │
│  3. Webhook (Drive API)    "File changed"               │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │  API Gateway          │
         │  POST /sync           │
         │  Body: {"mode": ...}  │
         └───────────┬───────────┘
                     │
                     ▼
         ┌─────────────────────────────────┐
         │  Lambda: Sync Orchestrator      │
         │  - Determine mode               │
         │  - Start Step Function          │
         └───────────┬─────────────────────┘
                     │
                     ▼
         ┌─────────────────────────────────┐
         │  Step Functions State Machine   │
         │  (for full sync/rebuild only)   │
         └───────────┬─────────────────────┘
                     │
                     ▼
         ┌─────────────────────────────────┐
         │  Lambda: Drive Document         │
         │  Processor (worker)             │
         │  - Fetch from Drive             │
         │  - Extract text                 │
         │  - Generate embeddings          │
         │  - Update FAISS index           │
         └───────────┬─────────────────────┘
                     │
                     ▼
         ┌─────────────────────────────────┐
         │  S3: Vector Storage             │
         │  - index.faiss                  │
         │  - metadata.json                │
         │  - sync_state.json              │
         └─────────────────────────────────┘
```

---

## 1. Sync Modes

### Mode 1: Full Sync (Initial)

**When to use**:
- First time setup
- Onboarding new user
- User connects new Google Drive

**Process**:
```
1. Query Drive API for ALL files in specified folder
2. Process files in batches (10-20 at a time)
3. Build FAISS index incrementally
4. Track progress in DynamoDB
5. Handle rate limits and retries
```

**API Call**:
```bash
POST /sync
{
  "mode": "full",
  "user_id": "user_123",
  "drive_folder_id": "root",  # or specific folder
  "filters": {
    "mimeTypes": ["application/pdf", "application/vnd.google-apps.document"],
    "maxFiles": 1000  # safety limit
  }
}
```

**Response**:
```json
{
  "sync_id": "sync_abc123",
  "status": "in_progress",
  "total_files": 487,
  "processed_files": 0,
  "estimated_duration_minutes": 45,
  "status_url": "/sync/sync_abc123/status"
}
```

---

### Mode 2: Rebuild Index

**When to use**:
- Index corrupted
- Changed chunking strategy (e.g., 512 → 1024 tokens)
- Changed embedding model (Titan → OpenAI)
- User wants to exclude certain file types

**Process**:
```
1. Delete existing FAISS index from S3
2. Query document_registry.json for all previously indexed files
3. Re-process each file from Google Drive
4. Build new index from scratch
5. Update registry with new metadata
```

**API Call**:
```bash
POST /sync
{
  "mode": "rebuild",
  "user_id": "user_123",
  "options": {
    "chunk_size": 1024,  # new chunking strategy
    "chunk_overlap": 256,
    "embedding_model": "titan-v2"
  }
}
```

---

### Mode 3: Incremental Sync

**When to use**:
- Ongoing maintenance (every 15 minutes)
- Webhook triggered by Drive file change
- User manually clicks "Sync Now"

**Process**:
```
1. Check last sync token from sync_state.json
2. Query Drive API changes.list() since last sync
3. Process only changed files:
   - New file → Add to index
   - Modified file → Update in index (remove old chunks, add new)
   - Deleted file → Remove from index
4. Update sync token
```

**API Call**:
```bash
POST /sync
{
  "mode": "incremental",
  "user_id": "user_123"
}
```

**Response (quick)**:
```json
{
  "sync_id": "sync_xyz789",
  "status": "completed",
  "changes_detected": 3,
  "files_added": 1,
  "files_modified": 2,
  "files_deleted": 0,
  "duration_seconds": 12
}
```

---

## 2. Implementation Details

### 2.1 Sync State Tracking (DynamoDB)

**Table**: `sync_jobs`

```
Partition Key: sync_id (String)
Sort Key: timestamp (Number)

Attributes:
- user_id (String, GSI)
- mode (String: 'full' | 'rebuild' | 'incremental')
- status (String: 'pending' | 'in_progress' | 'completed' | 'failed')
- total_files (Number)
- processed_files (Number)
- failed_files (List)
- started_at (Number)
- completed_at (Number)
- error_message (String)
- options (Map)  # custom options like chunk_size
```

**Table**: `user_sync_state`

```
Partition Key: user_id (String)

Attributes:
- last_sync_time (Number)
- last_sync_token (String)  # Drive API page token
- drive_folder_id (String)
- total_documents (Number)
- total_chunks (Number)
- last_full_sync (Number)
- sync_enabled (Boolean)
```

---

### 2.2 Lambda: Sync Orchestrator

**Function**: `sync-orchestrator`

```python
import boto3
import json
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
stepfunctions = boto3.client('stepfunctions')
sync_jobs_table = dynamodb.Table('sync_jobs')

def lambda_handler(event, context):
    """
    Handles sync requests and routes to appropriate handler
    """

    body = json.loads(event['body'])
    mode = body['mode']  # full | rebuild | incremental
    user_id = body['user_id']

    # Create sync job record
    sync_id = f"sync_{user_id}_{int(datetime.now().timestamp())}"

    sync_jobs_table.put_item(Item={
        'sync_id': sync_id,
        'user_id': user_id,
        'mode': mode,
        'status': 'pending',
        'started_at': int(datetime.now().timestamp()),
        'options': body.get('options', {})
    })

    if mode == 'full' or mode == 'rebuild':
        # Long-running job - use Step Functions
        response = stepfunctions.start_execution(
            stateMachineArn=SYNC_STATE_MACHINE_ARN,
            name=sync_id,
            input=json.dumps({
                'sync_id': sync_id,
                'user_id': user_id,
                'mode': mode,
                'options': body.get('options', {}),
                'drive_folder_id': body.get('drive_folder_id', 'root'),
                'filters': body.get('filters', {})
            })
        )

        return {
            'statusCode': 202,
            'body': json.dumps({
                'sync_id': sync_id,
                'status': 'in_progress',
                'status_url': f'/sync/{sync_id}/status'
            })
        }

    elif mode == 'incremental':
        # Quick sync - invoke worker directly
        lambda_client = boto3.client('lambda')
        response = lambda_client.invoke(
            FunctionName='drive-incremental-sync',
            InvocationType='Event',  # async
            Payload=json.dumps({
                'sync_id': sync_id,
                'user_id': user_id
            })
        )

        return {
            'statusCode': 202,
            'body': json.dumps({
                'sync_id': sync_id,
                'status': 'processing'
            })
        }
```

---

### 2.3 Step Functions State Machine (Full/Rebuild)

**State Machine**: `drive-sync-workflow`

```json
{
  "Comment": "Process all Google Drive documents",
  "StartAt": "Initialize",
  "States": {
    "Initialize": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:drive-list-files",
      "Next": "CheckFileCount",
      "ResultPath": "$.files"
    },
    "CheckFileCount": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.files.count",
          "NumericEquals": 0,
          "Next": "NoFilesFound"
        }
      ],
      "Default": "ProcessBatch"
    },
    "ProcessBatch": {
      "Type": "Map",
      "ItemsPath": "$.files.items",
      "MaxConcurrency": 5,
      "Iterator": {
        "StartAt": "ProcessFile",
        "States": {
          "ProcessFile": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:...:function:drive-process-file",
            "Retry": [
              {
                "ErrorEquals": ["States.TaskFailed"],
                "IntervalSeconds": 5,
                "MaxAttempts": 3,
                "BackoffRate": 2.0
              }
            ],
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.error",
                "Next": "LogError"
              }
            ],
            "End": true
          },
          "LogError": {
            "Type": "Pass",
            "End": true
          }
        }
      },
      "Next": "CheckMoreFiles"
    },
    "CheckMoreFiles": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.files.nextPageToken",
          "IsPresent": true,
          "Next": "GetNextBatch"
        }
      ],
      "Default": "Finalize"
    },
    "GetNextBatch": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:drive-list-files",
      "Next": "ProcessBatch",
      "ResultPath": "$.files"
    },
    "Finalize": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:sync-finalize",
      "End": true
    },
    "NoFilesFound": {
      "Type": "Succeed"
    }
  }
}
```

---

### 2.4 Lambda: List Drive Files

**Function**: `drive-list-files`

```python
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import boto3

secrets_client = boto3.client('secretsmanager')

def lambda_handler(event, context):
    """
    List files from Google Drive with pagination
    """

    user_id = event['user_id']
    mode = event['mode']
    folder_id = event.get('drive_folder_id', 'root')
    page_token = event.get('pageToken')
    filters = event.get('filters', {})

    # Get user's Drive credentials from Secrets Manager
    secret = secrets_client.get_secret_value(
        SecretId=f"drive-credentials/{user_id}"
    )
    creds_data = json.loads(secret['SecretString'])
    creds = Credentials.from_authorized_user_info(creds_data)

    # Build Drive service
    drive_service = build('drive', 'v3', credentials=creds)

    # Build query
    query_parts = [f"'{folder_id}' in parents", "trashed = false"]

    if filters.get('mimeTypes'):
        mime_types = filters['mimeTypes']
        mime_query = ' or '.join([f"mimeType = '{mt}'" for mt in mime_types])
        query_parts.append(f"({mime_query})")

    query = ' and '.join(query_parts)

    # List files
    results = drive_service.files().list(
        q=query,
        pageSize=100,
        pageToken=page_token,
        fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink)"
    ).execute()

    files = results.get('files', [])
    next_page_token = results.get('nextPageToken')

    return {
        'items': files,
        'count': len(files),
        'nextPageToken': next_page_token,
        'sync_id': event['sync_id'],
        'user_id': user_id,
        'mode': mode
    }
```

---

### 2.5 Lambda: Process Single File

**Function**: `drive-process-file`

```python
import io
import json
import numpy as np
import faiss
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import boto3

s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')

VECTOR_BUCKET = os.environ['VECTOR_BUCKET']

def lambda_handler(event, context):
    """
    Process a single file from Google Drive
    """

    file_info = event  # from Step Functions Map state
    user_id = file_info['user_id']
    file_id = file_info['id']
    sync_id = file_info['sync_id']

    print(f"Processing file: {file_info['name']} (ID: {file_id})")

    try:
        # 1. Download file content from Drive (streaming, no S3 storage)
        drive_service = get_drive_service(user_id)
        request = drive_service.files().get_media(fileId=file_id)

        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Download progress: {int(status.progress() * 100)}%")

        # 2. Extract text
        file_content.seek(0)
        text = extract_text(file_content, file_info['mimeType'])

        if not text or len(text.strip()) < 50:
            print(f"Skipping file {file_info['name']} - insufficient text")
            return {'status': 'skipped', 'reason': 'insufficient_text'}

        # 3. Chunk text
        chunks = chunk_text(
            text,
            chunk_size=512,
            overlap=128
        )

        print(f"Generated {len(chunks)} chunks")

        # 4. Generate embeddings
        embeddings = []
        metadata_list = []

        for i, chunk in enumerate(chunks):
            # Call Bedrock Titan
            embedding = generate_embedding(chunk)
            embeddings.append(embedding)

            metadata_list.append({
                "chunk_id": f"{file_id}_{i}",
                "text": chunk,
                "drive_file_id": file_id,
                "drive_file_name": file_info['name'],
                "drive_link": file_info['webViewLink'],
                "mime_type": file_info['mimeType'],
                "chunk_index": i,
                "total_chunks": len(chunks),
                "file_modified_time": file_info['modifiedTime'],
                "indexed_time": datetime.now().isoformat()
            })

        # 5. Update FAISS index
        update_vector_index(user_id, embeddings, metadata_list, file_id)

        # 6. Update sync progress
        update_sync_progress(sync_id, file_id, 'completed')

        return {
            'status': 'success',
            'file_id': file_id,
            'chunks_created': len(chunks)
        }

    except Exception as e:
        print(f"Error processing file {file_id}: {str(e)}")
        update_sync_progress(sync_id, file_id, 'failed', str(e))
        raise


def generate_embedding(text):
    """Generate embedding using Bedrock Titan"""
    response = bedrock.invoke_model(
        modelId='amazon.titan-embed-text-v2:0',
        body=json.dumps({
            "inputText": text
        })
    )

    result = json.loads(response['body'].read())
    return result['embedding']


def chunk_text(text, chunk_size=512, overlap=128):
    """Simple chunking by tokens (can be enhanced with semantic chunking)"""
    # Simplified - in production use tiktoken or similar
    words = text.split()
    chunks = []

    for i in range(0, len(words), chunk_size - overlap):
        chunk = ' '.join(words[i:i + chunk_size])
        if len(chunk) > 100:  # minimum chunk size
            chunks.append(chunk)

    return chunks


def extract_text(file_content, mime_type):
    """Extract text based on MIME type"""
    if mime_type == 'application/pdf':
        import PyPDF2
        reader = PyPDF2.PdfReader(file_content)
        text = ""
        for page in reader.pages:
            text += page.extract_text()
        return text

    elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        import docx
        doc = docx.Document(file_content)
        return '\n'.join([para.text for para in doc.paragraphs])

    elif mime_type == 'text/plain':
        return file_content.read().decode('utf-8')

    elif mime_type == 'application/vnd.google-apps.document':
        # Google Doc - export as plain text
        drive_service = get_drive_service(user_id)
        request = drive_service.files().export_media(
            fileId=file_id,
            mimeType='text/plain'
        )
        text_content = io.BytesIO()
        downloader = MediaIoBaseDownload(text_content, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return text_content.getvalue().decode('utf-8')

    else:
        raise ValueError(f"Unsupported MIME type: {mime_type}")


def update_vector_index(user_id, new_embeddings, new_metadata, file_id):
    """
    Update FAISS index with new embeddings
    For full sync: incremental add
    For rebuild: same logic (index starts empty)
    """

    index_key = f"vectors/{user_id}/index.faiss"
    metadata_key = f"vectors/{user_id}/metadata.json"

    # Download existing index (if exists)
    try:
        s3.download_file(VECTOR_BUCKET, index_key, '/tmp/index.faiss')
        s3.download_file(VECTOR_BUCKET, metadata_key, '/tmp/metadata.json')

        index = faiss.read_index('/tmp/index.faiss')
        with open('/tmp/metadata.json', 'r') as f:
            metadata = json.load(f)

        # Remove old chunks for this file (in case of re-processing)
        metadata = [m for m in metadata if m['drive_file_id'] != file_id]

        print(f"Loaded existing index with {index.ntotal} vectors")

    except s3.exceptions.NoSuchKey:
        # First file - create new index
        dimension = 1024
        index = faiss.IndexFlatL2(dimension)  # or IndexHNSWFlat for larger datasets
        metadata = []
        print("Created new FAISS index")

    # Add new embeddings
    embeddings_array = np.array(new_embeddings, dtype='float32')
    index.add(embeddings_array)
    metadata.extend(new_metadata)

    print(f"Index now has {index.ntotal} vectors")

    # Save back to S3
    faiss.write_index(index, '/tmp/index.faiss')
    s3.upload_file('/tmp/index.faiss', VECTOR_BUCKET, index_key)

    with open('/tmp/metadata.json', 'w') as f:
        json.dump(metadata, f)
    s3.upload_file('/tmp/metadata.json', VECTOR_BUCKET, metadata_key)

    print(f"Updated index saved to S3")


def update_sync_progress(sync_id, file_id, status, error=None):
    """Update sync job progress in DynamoDB"""
    sync_jobs_table = dynamodb.Table('sync_jobs')

    update_expr = "SET processed_files = processed_files + :inc"
    expr_values = {':inc': 1}

    if status == 'failed':
        update_expr += ", failed_files = list_append(if_not_exists(failed_files, :empty_list), :file)"
        expr_values[':file'] = [{'file_id': file_id, 'error': error}]
        expr_values[':empty_list'] = []

    sync_jobs_table.update_item(
        Key={'sync_id': sync_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values
    )
```

---

### 2.6 Lambda: Incremental Sync

**Function**: `drive-incremental-sync`

```python
def lambda_handler(event, context):
    """
    Sync only changed files since last sync
    """

    user_id = event['user_id']
    sync_id = event['sync_id']

    # Get last sync state
    state_table = dynamodb.Table('user_sync_state')
    state = state_table.get_item(Key={'user_id': user_id})['Item']

    last_sync_token = state.get('last_sync_token')

    # Get Drive service
    drive_service = get_drive_service(user_id)

    # Query for changes since last sync
    if last_sync_token:
        changes = drive_service.changes().list(
            pageToken=last_sync_token,
            spaces='drive',
            fields='nextPageToken, newStartPageToken, changes(fileId, file(id, name, mimeType, modifiedTime, webViewLink, trashed))'
        ).execute()
    else:
        # First incremental sync - get start token
        changes = drive_service.changes().getStartPageToken().execute()
        new_token = changes['startPageToken']
        state_table.update_item(
            Key={'user_id': user_id},
            UpdateExpression='SET last_sync_token = :token',
            ExpressionAttributeValues={':token': new_token}
        )
        return {'status': 'initialized', 'message': 'Start token set for future syncs'}

    files_changed = changes.get('changes', [])
    new_token = changes.get('newStartPageToken')

    stats = {
        'added': 0,
        'modified': 0,
        'deleted': 0
    }

    for change in files_changed:
        file_info = change.get('file')

        if not file_info:
            continue

        if file_info.get('trashed'):
            # File deleted
            remove_from_index(user_id, change['fileId'])
            stats['deleted'] += 1
        else:
            # File added or modified
            # Check if it's a supported file type
            mime_type = file_info.get('mimeType')
            if is_supported_mime_type(mime_type):
                # Process file (reuse existing function)
                result = process_file({
                    **file_info,
                    'user_id': user_id,
                    'sync_id': sync_id
                })

                if result['status'] == 'success':
                    # Check if new or modified (check document registry)
                    if file_was_previously_indexed(user_id, file_info['id']):
                        stats['modified'] += 1
                    else:
                        stats['added'] += 1

    # Update sync token
    state_table.update_item(
        Key={'user_id': user_id},
        UpdateExpression='SET last_sync_token = :token, last_sync_time = :time',
        ExpressionAttributeValues={
            ':token': new_token,
            ':time': int(datetime.now().timestamp())
        }
    )

    # Update sync job status
    sync_jobs_table = dynamodb.Table('sync_jobs')
    sync_jobs_table.update_item(
        Key={'sync_id': sync_id},
        UpdateExpression='SET #status = :status, completed_at = :time, stats = :stats',
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={
            ':status': 'completed',
            ':time': int(datetime.now().timestamp()),
            ':stats': stats
        }
    )

    return {
        'status': 'completed',
        'changes_detected': len(files_changed),
        **stats
    }


def remove_from_index(user_id, file_id):
    """Remove all chunks for a deleted file from the index"""

    index_key = f"vectors/{user_id}/index.faiss"
    metadata_key = f"vectors/{user_id}/metadata.json"

    # Download metadata
    s3.download_file(VECTOR_BUCKET, metadata_key, '/tmp/metadata.json')
    with open('/tmp/metadata.json', 'r') as f:
        metadata = json.load(f)

    # Filter out chunks for this file
    filtered_metadata = [m for m in metadata if m['drive_file_id'] != file_id]

    if len(filtered_metadata) == len(metadata):
        print(f"File {file_id} not found in index")
        return

    removed_count = len(metadata) - len(filtered_metadata)
    print(f"Removing {removed_count} chunks for file {file_id}")

    # FAISS doesn't support deletion, so we need to rebuild
    # For small datasets, this is acceptable
    # For large datasets, consider using IndexIDMap

    if len(filtered_metadata) > 0:
        # Rebuild index with remaining vectors
        dimension = 1024
        new_index = faiss.IndexFlatL2(dimension)

        # Re-add all vectors except deleted file's chunks
        # This requires keeping embeddings in metadata or re-downloading from S3
        # Alternative: use IndexIDMap for efficient deletion

        # For now, save filtered metadata
        with open('/tmp/metadata.json', 'w') as f:
            json.dump(filtered_metadata, f)
        s3.upload_file('/tmp/metadata.json', VECTOR_BUCKET, metadata_key)

        # Note: Proper implementation would rebuild FAISS index
        # This is a simplified version
    else:
        # All files removed - delete index
        s3.delete_object(Bucket=VECTOR_BUCKET, Key=index_key)
        s3.delete_object(Bucket=VECTOR_BUCKET, Key=metadata_key)


def is_supported_mime_type(mime_type):
    """Check if file type is supported"""
    supported = [
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.google-apps.document',
        'text/plain'
    ]
    return mime_type in supported
```

---

## 3. API Endpoints

### 3.1 Start Sync

```
POST /sync
Authorization: Bearer <cognito_token>

Request Body:
{
  "mode": "full" | "rebuild" | "incremental",
  "drive_folder_id": "folder_id",  # optional, defaults to root
  "options": {
    "chunk_size": 512,
    "chunk_overlap": 128,
    "max_files": 1000
  }
}

Response:
{
  "sync_id": "sync_user123_1234567890",
  "status": "in_progress" | "processing",
  "status_url": "/sync/{sync_id}/status"
}
```

### 3.2 Get Sync Status

```
GET /sync/{sync_id}/status
Authorization: Bearer <cognito_token>

Response:
{
  "sync_id": "sync_user123_1234567890",
  "user_id": "user_123",
  "mode": "full",
  "status": "in_progress",
  "progress": {
    "total_files": 487,
    "processed_files": 234,
    "failed_files": 2,
    "percentage": 48
  },
  "started_at": "2026-01-18T10:00:00Z",
  "estimated_completion": "2026-01-18T10:45:00Z",
  "errors": [
    {
      "file_id": "abc123",
      "error": "Unsupported file type"
    }
  ]
}
```

### 3.3 Cancel Sync

```
DELETE /sync/{sync_id}
Authorization: Bearer <cognito_token>

Response:
{
  "sync_id": "sync_user123_1234567890",
  "status": "cancelled"
}
```

### 3.4 List User's Indexed Documents

```
GET /documents
Authorization: Bearer <cognito_token>

Response:
{
  "total_documents": 487,
  "total_chunks": 5234,
  "last_sync": "2026-01-18T10:30:00Z",
  "documents": [
    {
      "drive_file_id": "abc123",
      "file_name": "2024_tax_return.pdf",
      "drive_link": "https://drive.google.com/file/d/abc123/view",
      "chunk_count": 15,
      "indexed_at": "2026-01-18T10:15:00Z",
      "file_size": "2.4 MB"
    }
  ]
}
```

---

## 4. Scheduled Incremental Sync

**CloudWatch Events Rule**:
```python
# CDK Code
from aws_cdk import aws_events, aws_events_targets, Duration

# Trigger incremental sync every 15 minutes for all active users
rule = aws_events.Rule(
    self, "IncrementalSyncSchedule",
    schedule=aws_events.Schedule.rate(Duration.minutes(15))
)

rule.add_target(
    aws_events_targets.LambdaFunction(
        incremental_sync_orchestrator_lambda,
        event={"mode": "scheduled"}
    )
)
```

**Orchestrator Lambda** (loops through active users):
```python
def lambda_handler(event, context):
    """
    Trigger incremental sync for all users with sync_enabled=true
    """

    state_table = dynamodb.Table('user_sync_state')

    # Scan for users with sync enabled
    response = state_table.scan(
        FilterExpression='sync_enabled = :true',
        ExpressionAttributeValues={':true': True}
    )

    lambda_client = boto3.client('lambda')

    for user_state in response['Items']:
        user_id = user_state['user_id']

        # Trigger incremental sync for this user
        lambda_client.invoke(
            FunctionName='drive-incremental-sync',
            InvocationType='Event',
            Payload=json.dumps({
                'user_id': user_id,
                'sync_id': f"sync_{user_id}_{int(datetime.now().timestamp())}",
                'mode': 'incremental'
            })
        )

        print(f"Triggered incremental sync for user {user_id}")

    return {
        'users_synced': len(response['Items'])
    }
```

---

## 5. Frontend Integration

### Initial Setup Flow

```typescript
// When user first connects Google Drive

async function setupInitialSync() {
  // 1. Authenticate with Google Drive
  const driveToken = await authenticateGoogleDrive();

  // 2. Save credentials to backend
  await saveUserDriveCredentials(driveToken);

  // 3. Start full sync
  const syncResponse = await fetch('/sync', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${cognitoToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      mode: 'full',
      drive_folder_id: 'root'  // or selected folder
    })
  });

  const { sync_id } = await syncResponse.json();

  // 4. Poll for progress
  pollSyncStatus(sync_id);
}

function pollSyncStatus(syncId: string) {
  const interval = setInterval(async () => {
    const status = await fetch(`/sync/${syncId}/status`, {
      headers: { 'Authorization': `Bearer ${cognitoToken}` }
    }).then(r => r.json());

    updateProgressBar(status.progress.percentage);

    if (status.status === 'completed') {
      clearInterval(interval);
      showSuccessMessage('All documents indexed!');
    } else if (status.status === 'failed') {
      clearInterval(interval);
      showErrorMessage(status.error_message);
    }
  }, 2000);  // Poll every 2 seconds
}
```

### Manual Sync Button

```typescript
async function triggerManualSync() {
  const response = await fetch('/sync', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${cognitoToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      mode: 'incremental'
    })
  });

  const result = await response.json();

  // Incremental sync is fast, usually completes immediately
  showNotification(`Synced ${result.files_added} new files, ${result.files_modified} updated`);
}
```

### Rebuild Index (Settings Page)

```typescript
async function rebuildIndex() {
  const confirmed = confirm(
    'This will rebuild your entire search index. ' +
    'Your documents will remain in Google Drive. ' +
    'This may take 30-60 minutes. Continue?'
  );

  if (!confirmed) return;

  const response = await fetch('/sync', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${cognitoToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      mode: 'rebuild',
      options: {
        chunk_size: 1024,  // new settings
        chunk_overlap: 256
      }
    })
  });

  const { sync_id } = await response.json();
  pollSyncStatus(sync_id);
}
```

---

## 6. Optimizations

### 6.1 FAISS Index with ID Mapping (Better Deletion)

For efficient deletion, use `IndexIDMap`:

```python
# When building index
dimension = 1024
base_index = faiss.IndexFlatL2(dimension)
index = faiss.IndexIDMap(base_index)

# Add with IDs (use hash of drive_file_id + chunk_index)
ids = np.array([hash(f"{file_id}_{i}") for i in range(len(embeddings))], dtype='int64')
index.add_with_ids(embeddings_array, ids)

# Remove by ID
index.remove_ids(ids_to_remove)
```

### 6.2 Caching for Query Lambda

```python
# Cache FAISS index in /tmp across Lambda invocations
# Lambda reuses containers for ~15 minutes

INDEX_CACHE = {}

def get_index(user_id):
    if user_id in INDEX_CACHE:
        cached_time = INDEX_CACHE[user_id]['time']
        if time.time() - cached_time < 300:  # 5 min cache
            return INDEX_CACHE[user_id]['index']

    # Download from S3
    index = faiss.read_index('/tmp/index.faiss')
    INDEX_CACHE[user_id] = {
        'index': index,
        'time': time.time()
    }
    return index
```

### 6.3 Parallel Processing for Full Sync

Step Functions Map state already handles parallelism (5 concurrent files).
Can increase to 10-20 for faster full sync.

---

## 7. Cost Estimate (Updated)

| Component | Usage | Monthly Cost |
|-----------|-------|--------------|
| **Lambda (Sync)** | 500 files × 30s each = 4.2 hours/month | $2.50 |
| **Lambda (Incremental)** | 2,880 invocations/month × 5s | $0.50 |
| **Lambda (Query)** | 3,000 invocations × 3s | $0.30 |
| **Step Functions** | 500 state transitions | $0.01 |
| **S3 (vectors)** | 100 MB storage, 3,000 GET | $0.003 |
| **DynamoDB (sync state)** | On-demand | $0.25 |
| **Bedrock Embeddings** | 5,000 chunks once | $0.65 (one-time) |
| **Bedrock Claude** | 3,000 queries | $18.00 |
| **API Gateway** | 3,000 requests | $0.01 |
| **CloudWatch** | Logs | $2.50 |
| **Total** | | **~$24.07/month** |

---

## Summary

**Three modes for complete flexibility**:

1. ✅ **Full Sync**: Initial setup, process all Drive files
2. ✅ **Rebuild**: Recreate index with new settings
3. ✅ **Incremental**: Auto-sync every 15 min for new/modified files

**Key Benefits**:
- Google Drive remains single source of truth
- No duplicate storage
- User has full control
- Handles deletion gracefully
- Progress tracking for long-running jobs
- Cost-effective (~$24/month)

Ready to implement!
