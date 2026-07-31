# 기본값은 scripts/aws-up.sh 의 현행 값과 동일하게 유지한다(도입 시 환경 이름 변화 없음).
variable "project_slug" {
  description = "리소스 이름 접두어"
  type        = string
  default     = "kubeheal"
}

variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "공용 VPC CIDR"
  type        = string
  default     = "10.80.0.0/16"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes 버전"
  type        = string
  default     = "1.31"
}

# 클러스터 3개 — management(허브) + target 2개(스포크). aws-up.sh 의 명명과 동일.
variable "clusters" {
  description = "생성할 EKS 클러스터 정의 (key = 클러스터 이름 접미어)"
  type = map(object({
    instance_types = list(string)
    min_size       = number
    max_size       = number
    desired_size   = number
  }))
  default = {
    mgmt = {
      instance_types = ["t3.large"]
      min_size       = 2
      max_size       = 4
      desired_size   = 2
    }
    target-a = {
      instance_types = ["t3.medium"]
      min_size       = 1
      max_size       = 3
      desired_size   = 2
    }
    target-b = {
      instance_types = ["t3.medium"]
      min_size       = 1
      max_size       = 3
      desired_size   = 2
    }
  }
}

variable "ecr_repositories" {
  description = "ECR 저장소 이름 목록 (project_slug 접두어 없이)"
  type        = list(string)
  default     = ["service", "console"]
}
