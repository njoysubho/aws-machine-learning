# UI Features & User Workflows

## Overview

Enhanced user interface with document upload, tagging, and proactive deadline notifications.

---

## 1. Core UI Features

### Feature 1: Document Upload to Google Drive

**User Flow**:
```
1. User clicks "Upload Document" button
2. File picker opens (PDF, images, DOCX)
3. User selects file(s)
4. Modal appears:
   - Document name (auto-populated from filename, editable)
   - Tags (optional, dropdown + create new)
   - Upload button
5. File uploads to Google Drive
6. Auto-triggers incremental sync
7. Document available for querying in ~30 seconds
```

**UI Components**:
```tsx
// Upload Modal
<UploadModal>
  <FileInput accept=".pdf,.png,.jpg,.jpeg,.docx" multiple />

  <TextField
    label="Document Name"
    defaultValue={file.name}
    required
  />

  <TagSelector
    existingTags={tags}
    onCreateNew={handleCreateTag}
    placeholder="Select or create tags..."
    allowMultiple
  />

  <Button onClick={uploadToGoogleDrive}>
    Upload to Drive
  </Button>
</UploadModal>
```

**API Endpoint**:
```
POST /documents/upload
Content-Type: multipart/form-data

Request:
- file: binary (PDF/image)
- document_name: string
- tags: string[] (optional)

Response:
{
  "drive_file_id": "abc123",
  "document_name": "2024_tax_return.pdf",
  "tags": ["tax", "2024"],
  "drive_link": "https://drive.google.com/file/d/abc123/view",
  "sync_status": "processing",
  "estimated_ready_seconds": 30
}
```

---

### Feature 2: Tag Management System

**Tag Storage** (DynamoDB):
```
Table: document_tags

Partition Key: user_id (String)
Sort Key: tag_name (String)

Attributes:
- tag_name (String)
- color (String) # hex color for UI
- created_at (Number)
- document_count (Number) # how many docs have this tag
- is_system (Boolean) # predefined tags like "tax", "insurance"
```

**Predefined System Tags**:
```json
[
  { "name": "tax", "color": "#10b981", "icon": "💰" },
  { "name": "insurance", "color": "#3b82f6", "icon": "🛡️" },
  { "name": "medical", "color": "#ef4444", "icon": "🏥" },
  { "name": "legal", "color": "#8b5cf6", "icon": "⚖️" },
  { "name": "financial", "color": "#f59e0b", "icon": "💳" },
  { "name": "personal", "color": "#ec4899", "icon": "👤" },
  { "name": "receipt", "color": "#6366f1", "icon": "🧾" }
]
```

**Tag Assignment** (Metadata):
```
S3: vectors/{user_id}/metadata.json

Each chunk includes:
{
  "chunk_id": "abc123_0",
  "text": "...",
  "drive_file_id": "abc123",
  "document_name": "2024_tax_return.pdf",
  "tags": ["tax", "2024", "federal"],  # ← Tags here
  "upload_date": "2026-01-18",
  ...
}
```

**UI Flow for Tag Selection**:

**Scenario 1: User provides tags during upload**
```tsx
// User selects existing tags
<TagSelector
  value={["tax", "2024"]}
  onChange={setTags}
/>
// → Uploads with tags immediately
```

**Scenario 2: No tags provided**
```tsx
// After file upload, modal appears
<TagPromptModal>
  <p>Help categorize this document:</p>
  <p className="font-semibold">{documentName}</p>

  <TagSelector
    suggestions={getSmartTagSuggestions(documentName)}
    existingTags={userTags}
    onCreateNew={(newTag) => createTag(newTag)}
    placeholder="Select tags or create new..."
  />

  <Button onClick={saveTagsAndSync}>Continue</Button>
  <Button variant="ghost" onClick={skipTags}>Skip</Button>
</TagPromptModal>
```

**Scenario 3: Create tag on the go**
```tsx
// In TagSelector component
<Combobox>
  {existingTags.map(tag => (
    <Option value={tag.name} color={tag.color}>
      {tag.icon} {tag.name}
    </Option>
  ))}

  {/* If input doesn't match existing */}
  <CreateOption onClick={() => createTag(inputValue)}>
    + Create "{inputValue}"
  </CreateOption>
</Combobox>
```

**Smart Tag Suggestions** (AI-powered):
```python
def get_smart_tag_suggestions(document_name: str, file_content: str = None):
    """
    Use Claude to suggest relevant tags based on filename and content
    """

    prompt = f"""
    Suggest 2-3 relevant tags for this document:

    Filename: {document_name}
    Content preview: {file_content[:500] if file_content else "N/A"}

    Common tags: tax, insurance, medical, legal, financial, receipt, personal

    Return only tag names, comma-separated.
    """

    # Quick Bedrock call with Haiku (cheap)
    response = bedrock.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 50,
            "messages": [{
                "role": "user",
                "content": prompt
            }]
        })
    )

    tags = parse_tags_from_response(response)
    return tags  # ["tax", "2024", "federal"]
```

**API Endpoints**:

```
GET /tags
Response: List of all user's tags + system tags

POST /tags
Body: { "name": "my-tag", "color": "#abc123" }
Response: Created tag

PUT /documents/{drive_file_id}/tags
Body: { "tags": ["tax", "2024"] }
Response: Updated document metadata

GET /documents?tag=tax
Response: All documents with "tax" tag
```

---

### Feature 3: Ask Questions (Enhanced)

**Already covered in main spec, but with tag filtering**:

```tsx
// Chat interface with optional filters
<ChatInterface>
  <FilterBar>
    <TagFilter
      selected={selectedTags}
      onChange={setSelectedTags}
    />
    <DateRangeFilter
      from={dateFrom}
      to={dateTo}
    />
  </FilterBar>

  <MessageList messages={messages} />

  <MessageInput
    onSend={(query) => sendQuery(query, {
      tags: selectedTags,
      dateRange: { from, to }
    })}
  />
</ChatInterface>
```

**Query with filters**:
```
POST /query

{
  "query": "What was my total medical expenses?",
  "filters": {
    "tags": ["medical", "receipt"],
    "date_from": "2024-01-01",
    "date_to": "2024-12-31"
  },
  "session_id": "session_123"
}
```

**Backend filters chunks before vector search**:
```python
def query_with_filters(query, filters):
    # Load metadata
    metadata = load_metadata(user_id)

    # Filter chunks by tags and date
    filtered_chunks = [
        chunk for chunk in metadata
        if matches_filters(chunk, filters)
    ]

    # Extract embeddings for filtered chunks only
    filtered_indices = [chunk['index'] for chunk in filtered_chunks]

    # Vector search within filtered subset
    # ... rest of RAG pipeline
```

---

### Feature 4: Deadline Detection & Email Notifications

**Architecture**:
```
CloudWatch Events (daily)
    ↓
Lambda: deadline-detector
    ↓
Scan all documents for dates
    ↓
Extract deadlines using Claude
    ↓
Check if deadline within 7/30 days
    ↓
Send email via SES
```

**Deadline Extraction** (during document processing):

```python
def extract_deadlines_from_document(text: str, document_name: str):
    """
    Use Claude to extract important dates/deadlines
    """

    prompt = f"""
    Extract any important dates, deadlines, or expiration dates from this document.

    Document: {document_name}

    Content:
    {text[:2000]}

    Return as JSON array:
    [
      {{
        "date": "YYYY-MM-DD",
        "description": "Tax filing deadline",
        "type": "deadline" | "expiration" | "due_date"
      }}
    ]

    If no dates found, return empty array [].
    """

    response = bedrock.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "messages": [{
                "role": "user",
                "content": prompt
            }]
        })
    )

    deadlines = json.loads(response['content'][0]['text'])
    return deadlines
```

**Store Deadlines** (DynamoDB):
```
Table: document_deadlines

Partition Key: user_id (String)
Sort Key: deadline_date (String, ISO format)

Attributes:
- drive_file_id (String)
- document_name (String)
- deadline_date (String) # YYYY-MM-DD
- description (String)
- type (String) # deadline, expiration, due_date
- notification_sent (Boolean)
- created_at (Number)

GSI: deadline_date (for querying upcoming deadlines)
```

**Daily Deadline Checker Lambda**:
```python
import boto3
from datetime import datetime, timedelta
from email.mime.text import MIMEText

ses = boto3.client('ses')
dynamodb = boto3.resource('dynamodb')
deadlines_table = dynamodb.Table('document_deadlines')

def lambda_handler(event, context):
    """
    Check for upcoming deadlines and send email notifications
    """

    today = datetime.now().date()
    seven_days_from_now = today + timedelta(days=7)
    thirty_days_from_now = today + timedelta(days=30)

    # Query deadlines in next 30 days
    response = deadlines_table.query(
        IndexName='deadline_date_index',
        KeyConditionExpression='deadline_date BETWEEN :today AND :future',
        FilterExpression='notification_sent = :false',
        ExpressionAttributeValues={
            ':today': today.isoformat(),
            ':future': thirty_days_from_now.isoformat(),
            ':false': False
        }
    )

    deadlines_by_user = group_by_user(response['Items'])

    for user_id, deadlines in deadlines_by_user.items():
        # Get user email from Cognito
        user_email = get_user_email(user_id)

        # Group by urgency
        urgent = [d for d in deadlines if d['deadline_date'] <= seven_days_from_now]
        upcoming = [d for d in deadlines if d['deadline_date'] > seven_days_from_now]

        # Send email
        send_deadline_notification(user_email, urgent, upcoming)

        # Mark as notified
        for deadline in deadlines:
            mark_as_notified(deadline)

    return {'users_notified': len(deadlines_by_user)}


def send_deadline_notification(email, urgent, upcoming):
    """
    Send formatted email with deadlines
    """

    html_body = f"""
    <html>
      <body>
        <h2>📅 Upcoming Deadlines from Your Documents</h2>

        {format_urgent_section(urgent) if urgent else ''}
        {format_upcoming_section(upcoming) if upcoming else ''}

        <p style="color: #666; font-size: 12px;">
          Deadlines detected from your personal documents.
          <a href="https://yourapp.com/deadlines">Manage deadlines</a>
        </p>
      </body>
    </html>
    """

    ses.send_email(
        Source='notifications@yourdomain.com',
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {
                'Data': f'⚠️ {len(urgent)} Urgent Deadline(s) This Week'
            },
            'Body': {
                'Html': {'Data': html_body}
            }
        }
    )


def format_urgent_section(urgent):
    return f"""
    <div style="background: #fef2f2; padding: 16px; border-radius: 8px; margin: 16px 0;">
      <h3 style="color: #dc2626;">🚨 Urgent (Next 7 Days)</h3>
      <ul>
        {''.join([f'<li><strong>{d["description"]}</strong> - {d["deadline_date"]} ({d["document_name"]})</li>' for d in urgent])}
      </ul>
    </div>
    """


def format_upcoming_section(upcoming):
    return f"""
    <div style="background: #fef3c7; padding: 16px; border-radius: 8px; margin: 16px 0;">
      <h3 style="color: #d97706;">📌 Upcoming (Next 30 Days)</h3>
      <ul>
        {''.join([f'<li>{d["description"]} - {d["deadline_date"]} ({d["document_name"]})</li>' for d in upcoming])}
      </ul>
    </div>
    """
```

**Email Notification Settings** (User Preferences):

```
Table: user_preferences

Partition Key: user_id (String)

Attributes:
- email_notifications_enabled (Boolean)
- notification_frequency (String) # daily, weekly
- urgent_deadline_days (Number) # default: 7
- upcoming_deadline_days (Number) # default: 30
- notification_email (String) # override Cognito email
- notification_time (String) # "09:00" in user's timezone
```

**Frontend Settings Page**:
```tsx
<SettingsPage>
  <Section title="Deadline Notifications">
    <Toggle
      label="Enable email notifications"
      checked={notificationsEnabled}
      onChange={setNotificationsEnabled}
    />

    <Select
      label="Notification frequency"
      value={frequency}
      options={[
        { value: 'daily', label: 'Daily digest' },
        { value: 'weekly', label: 'Weekly summary' }
      ]}
    />

    <NumberInput
      label="Alert me when deadline is within (days)"
      value={urgentDays}
      min={1}
      max={30}
    />

    <TextField
      label="Send notifications to"
      type="email"
      value={email}
      placeholder="your@email.com"
    />

    <Button onClick={savePreferences}>Save</Button>
  </Section>
</SettingsPage>
```

---

## 2. Updated API Endpoints

### Document Management

```
POST   /documents/upload
  - Upload file to Google Drive
  - Auto-extract metadata and deadlines
  - Trigger incremental sync

GET    /documents
  - List all documents
  - Filter by tags, date range

GET    /documents/{drive_file_id}
  - Get document details + metadata

PUT    /documents/{drive_file_id}
  - Update document name, tags

DELETE /documents/{drive_file_id}
  - Delete from Drive and index

PUT    /documents/{drive_file_id}/tags
  - Update tags only
```

### Tag Management

```
GET    /tags
  - List all tags (system + user-created)

POST   /tags
  - Create new tag

PUT    /tags/{tag_name}
  - Update tag (color, etc.)

DELETE /tags/{tag_name}
  - Delete tag (doesn't delete documents)
```

### Deadlines

```
GET    /deadlines
  - List upcoming deadlines
  - Query params: ?days=30&type=urgent

GET    /deadlines/document/{drive_file_id}
  - Get deadlines for specific document

POST   /deadlines
  - Manually add deadline

PUT    /deadlines/{deadline_id}
  - Update deadline

DELETE /deadlines/{deadline_id}
  - Remove deadline

POST   /deadlines/extract/{drive_file_id}
  - Re-run deadline extraction for document
```

### User Preferences

```
GET    /preferences
  - Get user notification settings

PUT    /preferences
  - Update notification settings

POST   /preferences/test-email
  - Send test notification email
```

---

## 3. Updated Frontend Components

### 3.1 Dashboard View

```tsx
<Dashboard>
  {/* Quick stats */}
  <StatsBar>
    <Stat
      label="Total Documents"
      value={documentCount}
      icon={<FileIcon />}
    />
    <Stat
      label="Upcoming Deadlines"
      value={upcomingDeadlineCount}
      icon={<CalendarIcon />}
      variant="warning"
    />
    <Stat
      label="Last Synced"
      value={lastSyncTime}
      icon={<RefreshIcon />}
    />
  </StatsBar>

  {/* Urgent deadlines banner */}
  {urgentDeadlines.length > 0 && (
    <UrgentDeadlinesBanner deadlines={urgentDeadlines} />
  )}

  {/* Main content */}
  <Grid>
    <ChatPanel />
    <SidebarPanel>
      <RecentDocuments />
      <UpcomingDeadlines />
      <QuickActions />
    </SidebarPanel>
  </Grid>
</Dashboard>
```

### 3.2 Documents Library View

```tsx
<DocumentsLibrary>
  <Toolbar>
    <SearchBar placeholder="Search documents..." />
    <TagFilter tags={tags} />
    <DateRangeFilter />
    <UploadButton onClick={openUploadModal} />
  </Toolbar>

  <DocumentGrid>
    {documents.map(doc => (
      <DocumentCard
        key={doc.drive_file_id}
        name={doc.name}
        tags={doc.tags}
        uploadDate={doc.upload_date}
        thumbnail={doc.thumbnail_url}
        deadlines={doc.deadlines}
        onView={() => openInDrive(doc.drive_link)}
        onEdit={() => editDocument(doc)}
        onDelete={() => deleteDocument(doc)}
      />
    ))}
  </DocumentGrid>
</DocumentsLibrary>
```

### 3.3 Upload Flow (Complete)

```tsx
function UploadFlow() {
  const [file, setFile] = useState(null);
  const [documentName, setDocumentName] = useState('');
  const [tags, setTags] = useState([]);
  const [suggestedTags, setSuggestedTags] = useState([]);
  const [uploading, setUploading] = useState(false);

  const handleFileSelect = async (selectedFile) => {
    setFile(selectedFile);
    setDocumentName(selectedFile.name.replace(/\.[^/.]+$/, ''));

    // Get AI tag suggestions
    const suggestions = await getSmartTagSuggestions(selectedFile.name);
    setSuggestedTags(suggestions);
  };

  const handleUpload = async () => {
    setUploading(true);

    try {
      // Upload to backend
      const formData = new FormData();
      formData.append('file', file);
      formData.append('document_name', documentName);
      formData.append('tags', JSON.stringify(tags));

      const response = await fetch('/documents/upload', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${authToken}`
        },
        body: formData
      });

      const result = await response.json();

      // Show success message
      toast.success(
        `Document uploaded! Processing in ${result.estimated_ready_seconds}s`
      );

      // If no tags, prompt for them
      if (tags.length === 0) {
        openTagPrompt(result.drive_file_id);
      }

      // Close modal
      closeModal();

    } catch (error) {
      toast.error('Upload failed: ' + error.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <Modal>
      <FileDropzone onFileSelect={handleFileSelect} />

      {file && (
        <>
          <TextField
            label="Document Name"
            value={documentName}
            onChange={setDocumentName}
            required
          />

          <TagSelector
            value={tags}
            onChange={setTags}
            suggestions={suggestedTags}
            placeholder="Add tags (optional)..."
          />

          <Button
            onClick={handleUpload}
            loading={uploading}
            disabled={!documentName}
          >
            Upload to Drive
          </Button>
        </>
      )}
    </Modal>
  );
}
```

---

## 4. Mobile Optimizations

### 4.1 Mobile Upload (Camera Integration)

```tsx
<MobileUploadOptions>
  {/* File picker */}
  <Option onClick={openFilePicker}>
    📁 Choose from Files
  </Option>

  {/* Camera (mobile only) */}
  {isMobile && (
    <>
      <Option onClick={openCamera}>
        📸 Take Photo
      </Option>

      <Option onClick={scanDocument}>
        📄 Scan Document
      </Option>
    </>
  )}
</MobileUploadOptions>
```

**Camera Integration**:
```tsx
function CameraCapture() {
  const [stream, setStream] = useState(null);

  const startCamera = async () => {
    const mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment' } // Back camera
    });
    setStream(mediaStream);
  };

  const capturePhoto = () => {
    // Capture from video stream
    const canvas = document.createElement('canvas');
    const video = videoRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);

    // Convert to blob
    canvas.toBlob((blob) => {
      const file = new File([blob], 'captured_document.jpg', {
        type: 'image/jpeg'
      });
      handleFileSelect(file);
    }, 'image/jpeg', 0.9);
  };

  return (
    <CameraView>
      <video ref={videoRef} autoPlay />
      <CaptureButton onClick={capturePhoto} />
    </CameraView>
  );
}
```

### 4.2 Offline Support (PWA)

```tsx
// Service worker for offline upload queuing
self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('/documents/upload')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        // Queue for later
        return queueUploadForLater(event.request);
      })
    );
  }
});

// Sync when back online
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-uploads') {
    event.waitUntil(syncQueuedUploads());
  }
});
```

---

## 5. Implementation Priority

### Phase 1: Core Upload (Week 1-2)
- [ ] Upload modal UI
- [ ] Google Drive upload API
- [ ] Basic tag selector
- [ ] Auto-trigger incremental sync

### Phase 2: Tag System (Week 3)
- [ ] Tag management API
- [ ] Predefined system tags
- [ ] Create tag on-the-fly
- [ ] Tag filtering in search

### Phase 3: Smart Features (Week 4)
- [ ] AI tag suggestions
- [ ] Deadline extraction
- [ ] Deadline storage

### Phase 4: Notifications (Week 5)
- [ ] SES email setup
- [ ] Daily deadline checker
- [ ] User preferences
- [ ] Email templates

### Phase 5: Mobile & Polish (Week 6)
- [ ] Camera integration
- [ ] Mobile-optimized upload
- [ ] Offline support
- [ ] UI polish

---

## 6. Cost Impact

### Additional AWS Services

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| **SES (Email)** | 30 emails/month | $0.00 (free tier: 62K/month) |
| **Lambda (Deadline Checker)** | 30 invocations/month × 10s | $0.01 |
| **DynamoDB (Tags + Deadlines)** | 1 GB, on-demand | $0.50 |
| **Textract (OCR for images)** | 50 pages/month | $0.75 |
| **Total Additional** | | **~$1.26/month** |

**Updated total**: ~$27.42/month (was $26.16)

---

## 7. Security Considerations

### Upload Security

```python
# Validate file type
ALLOWED_MIME_TYPES = [
    'application/pdf',
    'image/jpeg',
    'image/png',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
]

# Validate file size (max 50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Scan for malware (optional, using ClamAV)
def scan_file(file_path):
    # AWS Lambda with ClamAV layer
    result = subprocess.run(['clamscan', file_path], capture_output=True)
    if result.returncode != 0:
        raise ValueError('File failed security scan')
```

### Email Security

- Verify email ownership (Cognito email verification)
- Rate limit notifications (max 5/day per user)
- Unsubscribe link in all emails
- SPF/DKIM/DMARC records for domain

---

## 8. User Experience Enhancements

### Deadline Visualization

```tsx
<CalendarView>
  {/* Timeline view */}
  <Timeline>
    {deadlines.map(deadline => (
      <TimelineEvent
        date={deadline.date}
        type={getUrgency(deadline.date)}
        title={deadline.description}
        document={deadline.document_name}
      />
    ))}
  </Timeline>

  {/* Calendar grid view */}
  <Calendar
    events={deadlines}
    onDateClick={showDeadlinesForDate}
  />
</CalendarView>
```

### Smart Insights

```tsx
<InsightsPanel>
  <Insight>
    💰 Total tax documents for 2024: 12
    <Button>View all</Button>
  </Insight>

  <Insight>
    🛡️ Your car insurance expires in 45 days
    <Button>Remind me</Button>
  </Insight>

  <Insight>
    📊 Most common tags: tax (45), receipt (32), insurance (18)
  </Insight>
</InsightsPanel>
```

---

## Summary

**New Capabilities**:
1. ✅ Upload documents directly to Google Drive from UI
2. ✅ Smart tagging with AI suggestions
3. ✅ Create/manage tags on-the-fly
4. ✅ Automatic deadline detection from documents
5. ✅ Email notifications for upcoming deadlines
6. ✅ Mobile camera integration for document capture
7. ✅ Offline upload queuing (PWA)
8. ✅ Calendar view for deadlines

**Total Cost**: ~$27.42/month (minimal increase)

**User Value**: Proactive document management with zero manual effort
