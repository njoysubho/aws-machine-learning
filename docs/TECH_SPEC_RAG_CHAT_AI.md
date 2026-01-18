# Technical Specification: Personal Document RAG Chat AI

## 1. Executive Summary

A privacy-first conversational AI system that enables natural language querying of personal documents (tax records, insurance policies, etc.) stored in Google Drive using Retrieval-Augmented Generation (RAG).

**Key Privacy Principles:**
- All data processing within user-controlled AWS infrastructure
- End-to-end encryption for data in transit and at rest
- No third-party data sharing
- User owns and controls all data and embeddings

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────┐
│          Google Drive (Source)          │
│   Tax docs, Insurance, Receipts, etc.   │
│      (Single Source of Truth)           │
└────────┬────────────────────────────────┘
         │
         │ Drive API (Stream files)
         ▼
┌─────────────────────────────────────────┐
│         AWS Cloud (Private VPC)         │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │  Lambda: Drive Sync & Process    │   │
│  │  - Fetch files from Drive        │   │
│  │  - Extract text (PDF, DOCX)      │   │
│  │  - Chunk content                 │   │
│  │  - Generate embeddings (Bedrock) │   │
│  │  - Build FAISS index             │   │
│  └──────────┬───────────────────────┘   │
│             │                            │
│             ▼                            │
│  ┌──────────────────────────────────┐   │
│  │   S3: Vector Storage             │   │
│  │   - index.faiss (vectors)        │   │
│  │   - metadata.json (chunks)       │   │
│  │   - sync_state.json              │   │
│  └──────────┬───────────────────────┘   │
│             │                            │
│             │ (Query time)               │
│             ▼                            │
│  ┌──────────────────────────────────┐   │
│  │  Lambda: Query Handler           │   │
│  │  1. Load FAISS from S3           │   │
│  │  2. Vector search                │   │
│  │  3. Retrieve chunks              │   │
│  │  4. Call Bedrock Claude          │   │
│  │  5. Return answer + citations    │   │
│  └──────────┬───────────────────────┘   │
│             │                            │
└─────────────┼────────────────────────────┘
              │ API Gateway (HTTPS)
              │ + Cognito Auth
              ▼
   ┌──────────────────────┐
   │  Vercel Frontend     │
   │  (Next.js + React)   │
   │  Mobile-Responsive   │
   └──────────────────────┘
```

### 2.2 Data Flow

**Ingestion** (Three Modes):
1. **Full Sync**: Process ALL Drive files → Extract text → Chunk → Embed → Build FAISS index → Save to S3
2. **Rebuild**: Delete existing index → Re-process all files with new settings
3. **Incremental**: Detect changed files → Process only new/modified → Update FAISS index

**Query Flow**:
1. User Query → Lambda loads FAISS from S3 → Vector search → Retrieve top-k chunks
2. Chunks + Query → Bedrock Claude → Generate answer with citations
3. Return response with source document links (Google Drive)

**Security**: All traffic encrypted (TLS 1.3), authentication via AWS Cognito

**See**: `DRIVE_SYNC_ARCHITECTURE.md` for detailed sync implementation

---

## 3. Component Specifications

### 3.1 RAG Pipeline

#### 3.1.1 Document Sync & Processing Service

**Technology**: AWS Lambda + Step Functions + Google Drive API

**Three Operational Modes**:
1. **Full Sync**: Process all files from Google Drive (first-time setup)
2. **Rebuild**: Recreate entire index with new settings
3. **Incremental**: Process only new/modified files (ongoing)

**Responsibilities**:
- Stream documents from Google Drive (no S3 storage)
- Support formats: PDF, DOCX, TXT, Google Docs, images (OCR via Textract)
- Extract text in-memory
- Chunk and generate embeddings
- Build/update FAISS index
- Track sync state in DynamoDB

**Implementation**:
```
Lambda: drive-sync-orchestrator
  - Routes to appropriate sync mode
  - Triggers Step Functions for full/rebuild
  - Direct invoke for incremental

Lambda: drive-process-file (worker)
  - Download file from Drive (streaming)
  - Extract text
  - Generate embeddings (Bedrock)
  - Update FAISS index in S3
  Runtime: Python 3.11
  Memory: 2048-3008 MB
  Timeout: 15 minutes

Step Functions: sync-workflow
  - Orchestrates full sync
  - Parallel processing (5-10 files at a time)
  - Error handling & retries
```

**Google Drive Integration**:
- OAuth 2.0 with Drive API v3
- Credentials stored in AWS Secrets Manager
- Scoped permissions: `drive.readonly`
- Incremental sync using Drive API change tokens (changes.list)
- Scheduled sync: Every 15 minutes via CloudWatch Events

**See**: `DRIVE_SYNC_ARCHITECTURE.md` for complete implementation details

#### 3.1.2 Document Processing Pipeline

**Technology**: AWS Lambda + Step Functions

**Steps**:
1. **Text Extraction**:
   - PDF: PyPDF2 / pdfplumber
   - DOCX: python-docx
   - Images: Amazon Textract
   - Output: Clean text with metadata

2. **Chunking Strategy**:
   - Semantic chunking (preserve context)
   - Chunk size: 512-1024 tokens
   - Overlap: 128 tokens
   - Preserve document structure (headers, sections)

3. **Metadata Enrichment**:
   ```json
   {
     "chunk_id": "uuid",
     "document_id": "google_drive_file_id",
     "document_name": "2024_tax_return.pdf",
     "document_type": "tax",
     "chunk_index": 0,
     "total_chunks": 15,
     "date_created": "2024-04-15",
     "date_indexed": "2026-01-18",
     "source_path": "Drive/Taxes/2024/"
   }
   ```

#### 3.1.3 Embedding Generation

**Technology**: Amazon Bedrock (Titan Embeddings v2)

**Configuration**:
- Model: `amazon.titan-embed-text-v2`
- Dimension: 1024 (or 1536 for compatibility)
- Normalization: L2 normalized vectors
- Batch processing: 25 chunks per API call

**Fallback**: OpenAI `text-embedding-3-small` (if Bedrock unavailable)

#### 3.1.4 Vector Storage

**Primary Option: S3 + FAISS (In-Memory)** ✅ Recommended

For small-medium datasets (<10,000 documents), store FAISS index directly in S3:

**Architecture**:
```
S3 Bucket Structure:
vectors/
  ├── user_123/
  │   ├── index.faiss          # FAISS index file (binary)
  │   ├── metadata.json        # Chunk text + Drive file links
  │   └── sync_state.json      # Last sync token, document registry
```

**Query-time behavior**:
1. Lambda downloads `index.faiss` from S3 (~100 MB, <1s)
2. Loads into Lambda memory (3008 MB allocation)
3. Performs vector search using FAISS
4. Results cached in Lambda `/tmp` for subsequent queries

**FAISS Configuration**:
```python
import faiss
import numpy as np

# For datasets <100K vectors: use flat index
dimension = 1024
index = faiss.IndexFlatL2(dimension)  # Exact search

# For 100K-1M vectors: use HNSW for speed
index = faiss.IndexHNSWFlat(dimension, 32)  # M=32, approximate search

# For deletions support: wrap with IndexIDMap
index = faiss.IndexIDMap(base_index)
index.add_with_ids(embeddings, ids)
index.remove_ids(ids_to_delete)  # Efficient deletion
```

**Pros**:
- ✅ **Cost**: ~$0.25/month vs $350/month (OpenSearch)
- ✅ **Privacy**: Complete control, no external service
- ✅ **Simplicity**: Just S3, no VPC networking
- ✅ **Good for <100K vectors**: Fast enough for personal use

**Cons**:
- ❌ Cold start: 500ms-2s to load index on first query
- ❌ Lambda memory limit: 10 GB max (~10M vectors)
- ❌ Manual index management: No built-in features

---

**Alternative for Large Scale: Amazon OpenSearch Serverless**

Use when:
- >10,000 documents
- >100 queries/day
- <1s latency required
- Real-time incremental updates

**Configuration**:
- Collection type: Vector search
- Deployed in private VPC
- KMS encryption
- Cost: ~$350/month

**Alternative: Pinecone Serverless**
- Cost: ~$70/month
- External service (consider privacy implications)

### 3.2 AI Agent

#### 3.2.1 Query Processing Lambda

**Function**: `query-handler`

**Runtime**: Python 3.11
**Memory**: 3008 MB (for optimal performance)
**Timeout**: 60 seconds
**Concurrency**: Reserved 10, Max 100

**Workflow**:
```python
def handle_query(user_query, session_id, user_id):
    # 1. Query understanding & rewriting
    enhanced_query = enhance_query(user_query)

    # 2. Vector search
    relevant_chunks = vector_search(
        query=enhanced_query,
        top_k=10,
        filters={"user_id": user_id}
    )

    # 3. Re-ranking (optional)
    reranked_chunks = rerank(relevant_chunks, user_query)

    # 4. Context construction
    context = build_context(reranked_chunks[:5])

    # 5. LLM generation
    response = generate_response(
        query=user_query,
        context=context,
        session_history=get_history(session_id)
    )

    # 6. Citation & source tracking
    return {
        "answer": response,
        "sources": extract_sources(reranked_chunks),
        "confidence": calculate_confidence(relevant_chunks)
    }
```

#### 3.2.2 LLM Configuration

**Primary Model**: Amazon Bedrock - Claude 3.5 Sonnet

**Model ID**: `anthropic.claude-3-5-sonnet-20241022-v2:0`

**Inference Parameters**:
```json
{
  "max_tokens": 2048,
  "temperature": 0.3,
  "top_p": 0.9,
  "stop_sequences": ["Human:", "Assistant:"]
}
```

**System Prompt**:
```
You are a personal document assistant with access to the user's private documents including tax records, insurance policies, and financial information.

Your responsibilities:
1. Answer questions accurately using ONLY the provided context
2. Cite specific documents when providing information
3. If information is not in the context, clearly state you don't have that information
4. Protect user privacy - never suggest sharing sensitive information
5. Format numbers and dates clearly (e.g., $50,000, April 15, 2024)

Context documents:
{context}

User question: {query}

Provide a clear, concise answer with citations.
```

**Fallback**: Claude 3 Haiku (for faster, simpler queries)

#### 3.2.3 Session Management

**Storage**: Amazon DynamoDB

**Table Schema**:
```
Table: chat-sessions
Partition Key: session_id (String)
Sort Key: timestamp (Number)

Attributes:
- user_id (String, GSI)
- message_id (String)
- role (String: 'user' | 'assistant')
- content (String)
- sources (List)
- created_at (Number)
- ttl (Number, 30 days)
```

**Context Window**: Last 10 messages (sliding window)

### 3.3 Frontend Application

#### 3.3.1 Technology Stack

**Framework**: Next.js 14+ (App Router)
**UI Library**:
- React 18
- Tailwind CSS
- shadcn/ui components
- Radix UI primitives

**State Management**:
- React Query (server state)
- Zustand (client state)

**Authentication**: AWS Amplify + Cognito

#### 3.3.2 Key Features

1. **Chat Interface**:
   - Mobile-first responsive design
   - Message streaming (SSE or WebSockets)
   - Code/table formatting
   - Source citations (expandable)
   - Tag/date filtering for queries
   - Loading states & error handling

2. **Document Upload & Management**: ⭐ NEW
   - Upload PDFs/images directly to Google Drive
   - Camera integration for mobile (scan documents)
   - Smart tag suggestions using AI
   - Create/manage tags on-the-fly
   - Tag-based document organization
   - Document library with filtering
   - View/edit/delete documents

3. **Deadline Tracking & Notifications**: ⭐ NEW
   - Automatic deadline extraction from documents
   - Calendar view of upcoming deadlines
   - Email notifications (7-day and 30-day alerts)
   - Configurable notification preferences
   - Manual deadline management

4. **Settings**:
   - Google Drive connection status
   - Privacy controls
   - Data retention settings
   - Notification preferences
   - Export chat history

**See**: `UI_FEATURES_SPEC.md` for detailed implementation

#### 3.3.3 Mobile Optimization

- Progressive Web App (PWA) capabilities
- Offline message queuing
- Touch-optimized UI (44px minimum tap targets)
- Responsive breakpoints: 320px, 768px, 1024px, 1440px
- Dark mode support

#### 3.3.4 Deployment (Vercel)

```
vercel.json:
{
  "framework": "nextjs",
  "buildCommand": "npm run build",
  "outputDirectory": ".next",
  "regions": ["iad1"],
  "env": {
    "NEXT_PUBLIC_API_URL": "@api-gateway-url",
    "NEXT_PUBLIC_COGNITO_USER_POOL_ID": "@cognito-pool-id",
    "NEXT_PUBLIC_COGNITO_CLIENT_ID": "@cognito-client-id"
  }
}
```

### 3.4 Backend API (AWS Lambda)

#### 3.4.1 API Endpoints

**Base URL**: `https://api.yourdomain.com/v1`

**Endpoints**:

```
# Query & Chat
POST   /query
GET    /sessions
GET    /sessions/{session_id}
DELETE /sessions/{session_id}

# Document Management
GET    /documents                      # List all docs (with tag/date filters)
POST   /documents/upload               # Upload to Google Drive ⭐ NEW
GET    /documents/{drive_file_id}
PUT    /documents/{drive_file_id}      # Update name/tags
DELETE /documents/{drive_file_id}
PUT    /documents/{drive_file_id}/tags # Update tags only ⭐ NEW

# Sync Operations
POST   /sync                           # Trigger full/rebuild/incremental
GET    /sync/{sync_id}/status
DELETE /sync/{sync_id}

# Tag Management ⭐ NEW
GET    /tags                           # List all tags
POST   /tags                           # Create new tag
PUT    /tags/{tag_name}
DELETE /tags/{tag_name}

# Deadline Management ⭐ NEW
GET    /deadlines                      # List upcoming deadlines
GET    /deadlines/document/{drive_file_id}
POST   /deadlines                      # Manually add deadline
PUT    /deadlines/{deadline_id}
DELETE /deadlines/{deadline_id}
POST   /deadlines/extract/{drive_file_id} # Re-extract deadlines

# User Preferences ⭐ NEW
GET    /preferences
PUT    /preferences
POST   /preferences/test-email         # Send test notification

# Health
GET    /health
```

#### 3.4.2 API Gateway Configuration

**Type**: HTTP API (lower latency, cheaper)

**Authentication**:
- AWS Cognito JWT authorizer
- API keys for service-to-service

**CORS**:
```json
{
  "allowOrigins": ["https://yourdomain.vercel.app"],
  "allowMethods": ["GET", "POST", "DELETE", "OPTIONS"],
  "allowHeaders": ["Content-Type", "Authorization"],
  "maxAge": 300
}
```

**Rate Limiting**:
- 100 requests per minute per user
- 1000 requests per day per user

**Throttling**:
- Burst: 200
- Rate: 100 req/sec

---

## 4. Security & Privacy Architecture

### 4.1 Data Privacy Controls

#### 4.1.1 Encryption

**At Rest**:
- S3: AES-256 (SSE-S3 or SSE-KMS)
- OpenSearch: AWS KMS encryption
- DynamoDB: KMS encryption
- Secrets: AWS Secrets Manager (automatic rotation)

**In Transit**:
- TLS 1.3 minimum
- Certificate pinning (mobile apps)
- No mixed content

#### 4.1.2 Access Control

**IAM Policies**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::user-documents-${user_id}/*",
      "Condition": {
        "StringEquals": {
          "s3:ExistingObjectTag/user_id": "${cognito:sub}"
        }
      }
    }
  ]
}
```

**Network Isolation**:
- All processing in private VPC subnets
- No internet gateway access for data plane
- VPC endpoints for AWS services
- Security groups: deny all by default

#### 4.1.3 Data Retention

**User Controls**:
- Automatic deletion: 90 days, 1 year, never
- Right to deletion (GDPR compliance)
- Data export (JSON format)

**Implementation**:
- S3 lifecycle policies
- DynamoDB TTL
- Vector DB namespace deletion

### 4.2 Authentication & Authorization

**User Authentication**: AWS Cognito

**User Pool Configuration**:
```
MFA: Optional (TOTP)
Password Policy:
  - Minimum 12 characters
  - Require uppercase, lowercase, numbers, symbols
Password Recovery: Email verification
Session Duration: 1 hour (refresh: 30 days)
```

**Authorization Levels**:
- User: Own documents only
- Admin: System monitoring (no document access)

### 4.3 Compliance & Auditing

**Audit Logging**:
- CloudTrail: All API calls
- CloudWatch Logs: Application logs
- Access logs: Who accessed which documents when

**Retention**: 1 year minimum

**Compliance Considerations**:
- GDPR: Right to access, deletion, portability
- CCPA: Privacy policy, opt-out mechanisms
- HIPAA: If health documents (requires BAA with AWS)

### 4.4 Security Monitoring

**AWS Services**:
- GuardDuty: Threat detection
- Security Hub: Security posture
- Config: Resource compliance

**Alerts**:
- Unauthorized access attempts
- Unusual query patterns
- Data exfiltration indicators
- Failed authentication spikes

---

## 5. Technology Stack Summary

| Component | Technology | Rationale |
|-----------|------------|-----------|
| **Frontend** | Next.js 14 + React | SSR/SSG, excellent mobile support, Vercel optimization |
| **Frontend Hosting** | Vercel | Zero-config, edge network, preview deployments |
| **Backend Runtime** | AWS Lambda (Python 3.11) | Serverless, auto-scaling, pay-per-use |
| **API Gateway** | AWS HTTP API | Low latency, native Cognito integration |
| **Authentication** | AWS Cognito | Managed service, OAuth/OIDC support |
| **LLM** | Amazon Bedrock (Claude 3.5 Sonnet) | Privacy (no data retention), high quality, AWS integration |
| **Embeddings** | Titan Embeddings v2 | AWS-native, cost-effective, performant |
| **Vector Storage** | S3 + FAISS (in-memory) | Cost-effective ($0.003/mo vs $350), simple, privacy-focused |
| **Document Source** | Google Drive | Single source of truth, no duplicate storage |
| **Session Storage** | DynamoDB | Serverless, fast, TTL support |
| **Sync State** | DynamoDB | Track sync jobs, user preferences |
| **Tags & Deadlines** | DynamoDB | Document organization, deadline tracking ⭐ NEW |
| **Email Notifications** | Amazon SES | Deadline alerts, free tier ⭐ NEW |
| **OCR** | Amazon Textract | Extract text from scanned images ⭐ NEW |
| **Orchestration** | Step Functions | Visual workflows, error handling, retries |
| **Secrets** | Secrets Manager | Automatic rotation, encryption |
| **Monitoring** | CloudWatch + X-Ray | Integrated logging, distributed tracing |
| **IaC** | AWS CDK (Python) | Type-safe, reusable constructs |

---

## 6. Implementation Phases

### Phase 1: Foundation (Weeks 1-2)

**Deliverables**:
- [ ] AWS account setup + VPC configuration
- [ ] Cognito user pool + test users
- [ ] S3 bucket with encryption
- [ ] Google Drive OAuth setup
- [ ] Basic document sync Lambda (Drive → S3)
- [ ] Manual sync tested with 10 sample documents

**Success Criteria**: Documents sync from Google Drive to S3

### Phase 2: RAG Pipeline (Weeks 3-4)

**Deliverables**:
- [ ] Document processing pipeline (text extraction, chunking)
- [ ] Embedding generation (Bedrock Titan)
- [ ] OpenSearch Serverless collection setup
- [ ] Ingestion Lambda (S3 → embeddings → OpenSearch)
- [ ] Vector search function
- [ ] Test with 100+ documents

**Success Criteria**: Vector search returns relevant chunks for test queries

### Phase 3: AI Agent (Weeks 5-6)

**Deliverables**:
- [ ] Query handler Lambda
- [ ] Bedrock Claude integration
- [ ] RAG orchestration (retrieval + generation)
- [ ] Session management (DynamoDB)
- [ ] API Gateway endpoints
- [ ] Postman/curl testing

**Success Criteria**: API returns accurate answers with citations

### Phase 4: Frontend (Weeks 7-8)

**Deliverables**:
- [ ] Next.js project setup
- [ ] Chat UI (mobile-responsive)
- [ ] AWS Amplify authentication
- [ ] API integration
- [ ] Document management UI
- [ ] Settings page
- [ ] Vercel deployment

**Success Criteria**: Working chat interface accessible from mobile

### Phase 5: Security Hardening (Week 9)

**Deliverables**:
- [ ] VPC private subnets + security groups
- [ ] KMS encryption for all data stores
- [ ] IAM policies (least privilege)
- [ ] API rate limiting
- [ ] Secrets rotation
- [ ] Security scanning (OWASP ZAP, Snyk)

**Success Criteria**: Pass security audit checklist

### Phase 6: Testing & Optimization (Week 10)

**Deliverables**:
- [ ] Load testing (Artillery, k6)
- [ ] Cost optimization (Lambda memory tuning)
- [ ] Embedding quality evaluation
- [ ] Response accuracy testing
- [ ] Mobile browser testing (iOS Safari, Chrome)
- [ ] Documentation

**Success Criteria**: <2s query response time, <$50/month cost (100 queries/day)

### Phase 7: Production Launch (Week 11)

**Deliverables**:
- [ ] Production environment setup
- [ ] Monitoring dashboards (CloudWatch)
- [ ] Alerts configuration
- [ ] Backup/disaster recovery plan
- [ ] User documentation
- [ ] Privacy policy

**Success Criteria**: System live and monitored

---

## 7. Cost Estimation (Monthly)

**Assumptions**:
- 500 documents (~5,000 chunks)
- 100 queries/day (~3,000/month)
- Single user
- Google Drive as source (no S3 document storage)
- S3 + FAISS for vector storage

### Primary Architecture (S3 + FAISS)

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| **Google Drive** | 15 GB storage | $0 (included) |
| **S3 (vectors only)** | 100 MB storage, 3,000 GET | $0.003 |
| **Lambda (Sync)** | 500 files/month × 30s = 4.2 hrs | $2.50 |
| **Lambda (Query)** | 3,000 invocations × 3s, 3008 MB | $1.80 |
| **Lambda (Deadline Checker)** | 30 invocations × 10s ⭐ NEW | $0.01 |
| **Bedrock (Claude Sonnet)** | 3,000 queries, 2K input + 1K output | $18.00 |
| **Bedrock (Titan Embeddings)** | 5,000 chunks × 512 tokens | $0.65 (one-time) |
| **Bedrock (Haiku - Tag Suggestions)** | 50 calls/month ⭐ NEW | $0.10 |
| **DynamoDB** | Sync + sessions + tags + deadlines ⭐ NEW | $1.75 |
| **Step Functions** | 500 state transitions/month | $0.01 |
| **API Gateway** | 3,500 requests (added upload) ⭐ NEW | $0.01 |
| **Cognito** | 1 MAU | Free |
| **SES (Email Notifications)** | 30 emails/month ⭐ NEW | Free |
| **Textract (OCR for images)** | 50 pages/month ⭐ NEW | $0.75 |
| **CloudWatch Logs** | 5 GB | $2.50 |
| **Vercel** | Hobby plan | $0 (or $20 Pro) |
| **Data Transfer** | 1 GB | $0.09 |
| **Total (Ongoing)** | | **~$28.17/month** |

**Initial Setup Cost**: +$0.65 (one-time embedding generation)

---

### Alternative: Large Scale (OpenSearch Serverless)

For >10,000 documents, >100 queries/day:

| Service | Monthly Cost |
|---------|--------------|
| Base architecture | $26.16 |
| **OpenSearch Serverless** | $350.00 |
| Remove S3 vector storage | -$0.003 |
| **Total** | **~$376/month** |

---

### Cost Optimization Tips

1. **Reduce Bedrock costs** (30-50% savings):
   - Cache frequent queries in DynamoDB
   - Use Claude Haiku for simple questions ($0.80 vs $18)

2. **Reduce Lambda costs**:
   - Tune memory allocation (test 1024MB vs 3008MB)
   - Use Lambda SnapStart (reduce cold starts)

3. **Incremental sync only**:
   - Skip full rebuilds unless necessary
   - Reduces Lambda compute time

**Optimized estimate**: **~$22/month** (with caching + Haiku for 50% of queries)

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Google Drive API rate limits** | Sync failures | Exponential backoff, batch processing, quota monitoring |
| **Bedrock throttling** | Slow responses | Request quotas increase, implement caching, fallback to Haiku |
| **Cost overruns** | Budget exceeded | CloudWatch billing alarms, usage quotas, cost explorer dashboards |
| **Sensitive data exposure** | Privacy breach | VPC isolation, encryption, access logging, regular audits |
| **Poor retrieval quality** | Wrong answers | Hybrid search (keyword + vector), reranking, user feedback loop |
| **Cold start latency** | Slow first query | Provisioned concurrency, Lambda SnapStart (Java), keep-warm functions |
| **Single point of failure** | Service outage | Multi-AZ deployment, retry logic, graceful degradation |

---

## 9. Success Metrics (KPIs)

### 9.1 Performance Metrics

- **Query Latency**: P50 < 1.5s, P95 < 3s, P99 < 5s
- **Retrieval Accuracy**: Top-5 contains answer >90% (manual evaluation)
- **Uptime**: 99.9% availability
- **Cold Start**: <500ms (Lambda SnapStart)

### 9.2 Quality Metrics

- **Answer Correctness**: >95% (human evaluation on sample queries)
- **Citation Accuracy**: 100% (citations must match sources)
- **Hallucination Rate**: <2% (answers must be grounded in documents)

### 9.3 User Experience Metrics

- **Mobile Usability**: Score >90 (Lighthouse)
- **Time to First Response**: <2s
- **Session Length**: >3 queries per session (indicates usefulness)

---

## 10. Future Enhancements (Post-MVP)

### Phase 2 Features

1. **Multi-modal Support**:
   - Image understanding (extract data from scanned documents)
   - Table extraction and querying
   - Chart/graph interpretation

2. **Advanced Agent Capabilities**:
   - Multi-step reasoning (chain-of-thought)
   - Calculation tools (for tax/financial queries)
   - Calendar integration (appointment reminders from emails)

3. **Collaboration**:
   - Share specific documents with family members
   - Household access (multiple users, shared documents)

4. **Proactive Insights**:
   - "Your insurance premium increases next month"
   - "Tax filing deadline approaching"
   - Document expiration alerts

5. **Additional Data Sources**:
   - Dropbox, OneDrive integration
   - Email (Gmail API for receipts, confirmations)
   - Local file upload

6. **Voice Interface**:
   - Speech-to-text (AWS Transcribe)
   - Text-to-speech responses (Polly)
   - Mobile app with voice commands

---

## 11. Development Setup

### 11.1 Prerequisites

- **AWS Account** with admin access
- **Google Cloud Project** (for Drive API)
- **Node.js** 18+ and npm
- **Python** 3.11+
- **AWS CDK** installed (`npm install -g aws-cdk`)
- **Git** and GitHub account

### 11.2 Repository Structure

```
rag-personal-assistant/
├── backend/
│   ├── lambda/
│   │   ├── document_sync/
│   │   ├── document_processor/
│   │   ├── query_handler/
│   │   └── shared/           # Common utilities
│   ├── infrastructure/       # AWS CDK code
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/             # Next.js app directory
│   │   ├── components/
│   │   ├── lib/
│   │   └── hooks/
│   ├── public/
│   ├── package.json
│   └── next.config.js
├── docs/
│   ├── TECH_SPEC.md         # This document
│   ├── API.md
│   └── DEPLOYMENT.md
├── .github/
│   └── workflows/           # CI/CD pipelines
└── README.md
```

### 11.3 Local Development

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt  # pytest, black, mypy

# Run tests
pytest tests/

# Frontend
cd frontend
npm install
npm run dev  # localhost:3000

# Infrastructure
cd backend/infrastructure
cdk synth    # Preview CloudFormation
cdk deploy   # Deploy to AWS
```

---

## 12. Deployment Checklist

### Pre-Deployment

- [ ] AWS account configured (billing alerts set)
- [ ] Domain purchased (optional but recommended)
- [ ] Google Drive API credentials obtained
- [ ] Environment variables documented
- [ ] Security review completed

### Infrastructure

- [ ] VPC with private/public subnets
- [ ] Cognito user pool created
- [ ] S3 bucket with versioning + encryption
- [ ] Lambda functions deployed
- [ ] API Gateway configured
- [ ] OpenSearch/Pinecone initialized
- [ ] DynamoDB tables created
- [ ] CloudWatch dashboards set up

### Application

- [ ] Frontend deployed to Vercel
- [ ] API endpoints tested (Postman collection)
- [ ] Authentication flow working
- [ ] Document sync tested
- [ ] Chat interface functional
- [ ] Mobile responsive verified

### Security

- [ ] HTTPS enforced
- [ ] CORS configured correctly
- [ ] API rate limiting active
- [ ] Secrets rotated
- [ ] IAM policies reviewed (least privilege)
- [ ] Security groups locked down
- [ ] Logging enabled

### Monitoring

- [ ] CloudWatch alarms configured
- [ ] Error tracking (Sentry optional)
- [ ] Cost monitoring alerts
- [ ] Uptime monitoring (UptimeRobot optional)

---

## 13. Contact & Support

**Project Owner**: [Your Name]
**AWS Account ID**: [Your Account]
**Estimated Timeline**: 11 weeks
**Budget**: ~$28/month (optimized: ~$22/month with caching)

**Note**: See `UI_FEATURES_SPEC.md` for upload, tagging, and notification features

---

## Appendix A: Sample Queries

Test cases for validation:

1. "What was my total income in 2024?" → Tax document
2. "When does my car insurance expire?" → Insurance policy
3. "Show me all medical expenses from last year" → Multiple receipts
4. "What's my deductible for health insurance?" → Policy document
5. "Do I have documentation for the home office deduction?" → Tax receipts

---

## Appendix B: Privacy Policy Checklist

Required disclosures:

- [ ] What data is collected (documents from Google Drive)
- [ ] How data is used (embeddings, search, LLM processing)
- [ ] Where data is stored (AWS S3, OpenSearch)
- [ ] Who has access (only you, no third-party sharing)
- [ ] How long data is retained (user-configurable)
- [ ] User rights (access, deletion, export)
- [ ] Security measures (encryption, access controls)
- [ ] Contact information for privacy concerns

---

**Document Version**: 1.0
**Last Updated**: 2026-01-18
**Status**: Draft for Review
