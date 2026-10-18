"""SAA-C03 공식 4개 도메인 키워드 기반 분류기."""

DOMAIN_NAMES = {
    1: "Design Secure Architectures",
    2: "Design Resilient Architectures",
    3: "Design High-Performing Architectures",
    4: "Design Cost-Optimized Architectures",
}

# 도메인별 키워드 (공식 시험 가이드 기반)
_KEYWORDS: dict[int, list[str]] = {
    1: [  # Design Secure Architectures (30%)
        "iam", "iam role", "iam policy", "permission", "least privilege",
        "sts", "assume role", "temporary credential",
        "cognito", "user pool", "identity pool",
        "kms", "key management", "customer managed key", "cmk", "envelope encryption",
        "cloudhsm", "hardware security",
        "acm", "certificate manager", "ssl", "tls", "https",
        "waf", "web application firewall", "aws shield", "ddos",
        "network firewall", "aws firewall manager",
        "guardduty", "aws inspector", "amazon macie", "security hub", "aws detective",
        "secrets manager", "parameter store", "ssm parameter",
        "cloudtrail", "audit log", "compliance",
        "aws config", "config rule",
        "mfa", "multi-factor", "identity federation", "saml", "oauth", "openid",
        "bucket policy", "s3 acl", "resource-based policy",
        "vpc endpoint policy", "service control policy", "scp", "organizations",
        "data at rest", "data in transit", "encryption",
        "security group", "nacl", "network access control",
        "private subnet", "public subnet", "bastion",
        "aws artifact", "aws audit manager",
    ],
    2: [  # Design Resilient Architectures (26%)
        "auto scaling", "autoscaling", "scale out", "scale in", "scale up",
        "load balancer", "alb", "nlb", "clb", "elb", "gwlb", "elastic load balancing",
        "multi-az", "multi az", "availability zone", "high availability",
        "fault toleran", "resilient", "self-healing",
        "failover", "disaster recovery", "rto", "rpo",
        "cross-region replication", "crr", "same-region replication", "srr",
        "s3 replication", "versioning",
        "route 53", "health check", "dns failover", "routing policy",
        "sqs", "dead letter queue", "dlq", "message queue", "visibility timeout",
        "sns", "eventbridge", "event bus", "event-driven",
        "step functions", "workflow", "orchestration",
        "pilot light", "warm standby", "active-active", "active-passive",
        "elastic beanstalk", "cloudformation", "infrastructure as code",
        "decouple", "loosely coupled", "microservice", "service mesh",
        "aws backup", "backup plan", "recovery point",
        "rds multi-az", "aurora replica", "global tables",
        "ecs", "eks", "fargate", "container",
        "lambda", "serverless", "event source",
    ],
    3: [  # Design High-Performing Architectures (24%)
        "instance type", "compute optimized", "memory optimized", "storage optimized",
        "placement group", "cluster placement", "spread placement", "partition placement",
        "enhanced networking", "ena", "sr-iov", "efa",
        "ebs", "io1", "io2", "gp2", "gp3", "provisioned iops", "throughput optimized", "st1", "sc1",
        "instance store", "nvme",
        "efs", "elastic file system",
        "fsx", "fsx for lustre", "fsx for windows", "fsx for netapp", "fsx for openzfs",
        "s3 transfer acceleration", "multipart upload", "byte-range fetch",
        "elasticache", "redis", "memcached", "in-memory cache",
        "dax", "dynamodb accelerator",
        "aurora", "aurora serverless", "aurora global database",
        "rds proxy", "read replica",
        "kinesis data streams", "kinesis firehose", "kinesis analytics", "msk", "apache kafka",
        "emr", "hadoop", "spark", "hive",
        "glue", "etl", "data catalog", "spark",
        "athena", "s3 select", "query",
        "redshift", "data warehouse", "redshift spectrum",
        "opensearch", "elasticsearch",
        "cloudfront", "cdn", "edge location", "origin shield",
        "global accelerator", "anycast ip",
        "direct connect", "dedicated connection", "hosted connection",
        "transit gateway", "vpc peering", "vpc endpoint",
        "graviton", "arm-based",
        "api gateway", "websocket", "rest api",
    ],
    4: [  # Design Cost-Optimized Architectures (20%)
        "cost", "price", "pricing", "billing", "saving", "cheap", "inexpensive",
        "reduce cost", "minimize cost", "cost-effective", "cost efficient",
        "reserved instance", "reserved capacity", "1-year", "3-year commitment",
        "spot instance", "spot fleet", "spot request", "interruption",
        "savings plan", "compute savings plan", "ec2 instance savings plan",
        "s3 intelligent-tiering", "s3-ia", "s3 one zone", "s3 glacier",
        "glacier instant retrieval", "glacier flexible retrieval", "glacier deep archive",
        "lifecycle policy", "lifecycle rule", "storage class transition",
        "cost explorer", "aws budgets", "cost allocation tag",
        "trusted advisor", "aws compute optimizer",
        "right-sizing", "rightsizing", "idle resource",
        "serverless", "pay per use", "pay-per-use", "pay per request",
        "nat gateway cost", "data transfer",
        "on-demand", "on demand capacity",
        "underutilized", "over-provisioned",
        "free tier", "aws free",
        "tiered pricing", "volume discount",
    ],
}


def classify_domain(text: str) -> int:
    """키워드 빈도 점수로 SAA-C03 도메인(1-4)을 반환한다."""
    lower = text.lower()
    scores = {d: 0 for d in range(1, 5)}
    for domain, keywords in _KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[domain] += 1
    best = max(scores, key=lambda d: scores[d])
    # 동점 또는 매칭 없으면 출제 비중 최고인 도메인 1(보안) 반환
    return best if scores[best] > 0 else 1


def domain_name(domain: int) -> str:
    return DOMAIN_NAMES[domain]
