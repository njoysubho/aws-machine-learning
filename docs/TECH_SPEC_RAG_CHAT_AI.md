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
┌─────────────────┐
│  Google Drive   │
│  (Data Source)  │
└────────┬────────┘
         │
         │ Secure Sync
         ▼
┌─────────────────────────────────────────┐
│         AWS Cloud (Private VPC)         │
│                                         │
│  ┌──────────────┐    ┌───────────────┐ │
│  │ S3 Bucket    │    │   Lambda      │ │
│  │ (Encrypted)  │───▶│   Ingestion   │ │
│  │              │    │   Pipeline    │ │
│  └──────────────┘    └───────┬───────┘ │
│                              │          │
│                              ▼          │
│  ┌──────────────┐    ┌───────────────┐ │
│  │   Amazon     │◀───│  Embedding    │ │
│  │   Bedrock    │    │  Generation   │ │
│  │   (Claude)   │    └───────────────┘ │
│  └──────┬───────┘            │          │
│         │                    ▼          │
│         │            ┌───────────────┐  │
│         │            │   OpenSearch  │  │
│         │            │ Serverless/   │  │
│         │            │   Pinecone    │  │
│         │            │ (Vector DB)   │  │
│         │            └───────┬───────┘  │
│         │                    │          │
│         │                    │          │
│         ▼                    ▼          │
│  ┌────────────────────────────────────┐ │
│  │     Lambda Functions (Agent)       │ │
│  │  - Query Handler                   │ │
│  │  - RAG Orchestrator                │ │
│  │  - Document Retrieval              │ │
│  └────────────────┬───────────────────┘ │
│                   │                      │
└───────────────────┼──────────────────────┘
                    │ API Gateway (HTTPS)
                    │ + Cognito Auth
                    ▼
         ┌──────────────────┐
         │  Vercel Frontend │
         │  (React/Next.js) │
         │  Mobile-Friendly │
         └──────────────────┘
```

### 2.2 Data Flow

1. **Ingestion**: Google Drive → S3 (encrypted) → Document Processing → Embedding Generation → Vector Store
2. **Query**: User Query → Lambda Agent → Vector Search → Document Retrieval → LLM Context → Response
3. **Security**: All traffic encrypted (TLS 1.3), authentication via AWS Cognito

---

## 3. Component Specifications

### 3.1 RAG Pipeline

#### 3.1.1 Document Ingestion Service

**Technology**: AWS Lambda + S3

**Responsibilities**:
- Sync documents from Google Drive to encrypted S3 bucket
- Support formats: PDF, DOCX, TXT, images (OCR via Textract)
- Metadata extraction (filename, date, document type)
- Change detection (only process new/modified files)

**Implementation**:
```
Lambda Function: document-ingest-service
Runtime: Python 3.11
Memory: 2048 MB
Timeout: 15 minutes
Trigger: CloudWatch Events (scheduled) + Manual S3 events
```

**Google Drive Integration**:
- OAuth 2.0 with Drive API v3
- Credentials stored in AWS Secrets Manager
- Scoped permissions: `drive.readonly`
- Incremental sync using Drive API change tokens

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

#### 3.1.4 Vector Database

**Primary Option**: Amazon OpenSearch Serverless

**Configuration**:
- Collection type: Vector search
- Engine: FAISS-based ANN
- Index settings:
  ```json
  {
    "settings": {
      "index.knn": true,
      "index.knn.algo_param.ef_search": 512
    },
    "mappings": {
      "properties": {
        "embedding": {
          "type": "knn_vector",
          "dimension": 1024,
          "method": {
            "name": "hnsw",
            "space_type": "cosinesimilarity",
            "engine": "faiss"
          }
        },
        "text": {"type": "text"},
        "metadata": {"type": "object"}
      }
    }
  }
  ```

**Alternative**: Pinecone (Serverless)
- Index: 1024 dimensions, cosine similarity
- Namespace: per-user isolation
- Metadata filtering enabled

**Privacy**:
- OpenSearch deployed in private VPC subnets
- No public endpoints
- Encryption at rest (AWS KMS)

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
   - Loading states & error handling

2. **Document Management**:
   - View synced documents
   - Manual sync trigger
   - Document preview
   - Delete/exclude documents

3. **Settings**:
   - Google Drive connection status
   - Privacy controls
   - Data retention settings
   - Export chat history

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
POST   /query
GET    /sessions
GET    /sessions/{session_id}
DELETE /sessions/{session_id}
GET    /documents
POST   /documents/sync
DELETE /documents/{document_id}
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
| **Vector Database** | OpenSearch Serverless | AWS-native, serverless, vector search optimized |
| **Document Storage** | S3 | Durable, encrypted, lifecycle management |
| **Session Storage** | DynamoDB | Serverless, fast, TTL support |
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

| Service | Usage | Cost |
|---------|-------|------|
| **S3** | 5 GB storage, 500 GET requests | $0.12 |
| **Lambda** | 3,000 invocations, 512MB, 5s avg | $0.50 |
| **Bedrock (Claude Sonnet)** | 3,000 queries, 2K input + 1K output tokens | $18.00 |
| **Bedrock (Titan Embeddings)** | 5,000 chunks × 512 tokens | $0.65 |
| **OpenSearch Serverless** | 1 OCU (indexing + search) | $350.00 |
| **DynamoDB** | 5 GB storage, on-demand | $1.25 |
| **API Gateway** | 3,000 requests | $0.01 |
| **Cognito** | 1 MAU | Free |
| **CloudWatch** | 5 GB logs | $2.50 |
| **Vercel** | Hobby plan | $0 (or $20 Pro) |
| **Data Transfer** | 10 GB | $0.90 |
| **Total** | | **~$373.93/month** |

**Cost Optimization Options**:
1. **Replace OpenSearch with Pinecone Serverless**: ~$70/month → Save $280/month
2. **Use self-hosted pgvector (RDS Aurora Serverless v2)**: ~$45/month → Save $305/month
3. **Cache frequent queries**: Reduce Bedrock costs by 30-50%
4. **Reserved capacity**: If usage predictable

**Optimized Estimate with Pinecone**: **~$94/month**

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
**Budget**: ~$400/month (optimized: ~$100/month)

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
