# EKS 클러스터 3개 — for_each 로 동일 구조 반복(mgmt / target-a / target-b).
# 공식 모듈 사용: https://github.com/terraform-aws-modules/terraform-aws-eks
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  for_each = var.clusters

  cluster_name    = "${var.project_slug}-${each.key}"
  cluster_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # 데모/실습 편의 — 퍼블릭 엔드포인트 허용(프로덕션은 프라이빗 + VPN 권장)
  cluster_endpoint_public_access = true

  # 생성한 IAM 주체(팀원·CI)가 곧바로 kubectl 을 쓸 수 있게 admin 권한 부여
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = {}
    aws-ebs-csi-driver     = {} # storage.yaml 의 gp3 PVC 용
    eks-pod-identity-agent = {}
  }

  eks_managed_node_groups = {
    default = {
      instance_types = each.value.instance_types
      min_size       = each.value.min_size
      max_size       = each.value.max_size
      desired_size   = each.value.desired_size

      # EBS CSI 가 노드에서 볼륨을 붙일 수 있게
      iam_role_additional_policies = {
        ebs_csi = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
      }
    }
  }
}
