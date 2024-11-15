terraform {
  backend "s3" {
    bucket = "apps-tf"
    key    = "loan-approval-predictor-service.tfstate"
    region = "eu-west-1"
  }    
}

provider "aws" {
  region = "eu-west-1"
}