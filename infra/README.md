# sed.i Infrastructure

Pulumi Python project for AWS resources. Uses Poetry for dependency management (same as the backend).

## Prerequisites

1. [Install Pulumi](https://www.pulumi.com/docs/install/) — `brew install pulumi/tap/pulumi`
2. AWS credentials with IAM admin access (to create the Bedrock user and policy)
3. Log in to Pulumi state backend — `pulumi login`

## Setup

```bash
cd infra
poetry install
pulumi stack init dev
```

## Before enabling Bedrock

### Step 1: Enable model access in AWS console

Go to AWS Bedrock → Model access and request:

- Claude Haiku 4.5
- Claude Sonnet 4.5
- Amazon Titan Embed v2

Access is typically granted within minutes.

### Step 2: Deploy infra (creates IAM users + budget alarm)

```bash
pulumi up
```

### Step 3: Copy credentials to `.env`

```bash
pulumi stack output env_snippet
# Copy the output into content-queue-backend/.env
```

### Step 4: Test with evals

```bash
cd content-queue-backend
PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/evals/test_mcp_evals.py -v
```

### Step 5: Compare eval scores

Run the full eval suite with both providers and record the delta before switching production traffic. See ADR-0003 for the migration criteria.

## Resources managed

| Layer | Resource | Purpose |
|---|---|---|
| 4 | `sedi-bedrock-app-{env}` IAM user | Railway production credentials |
| 4 | `sedi-bedrock-dev-{env}` IAM user | Local development credentials |
| 4 | `sedi-bedrock-{env}` IAM policy | InvokeModel on specific model ARNs only |
| 4 | `sedi-bedrock-monthly-{env}` Budget | $20/month alarm — set before enabling traffic |
| 6 | S3 bucket (planned) | PDF and media asset storage |
