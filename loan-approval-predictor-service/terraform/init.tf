terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "5.75.1"
    }
  }
  backend "s3" {
    bucket = "apps-tf"
    key    = "${var.service_name}-${var.environment}.tfstate"
    region = "eu-west-1"
  }    
}