import sagemaker
from sagemaker.estimator import Estimator
import boto3

# Initialize the session
region = 'eu-central-1'
boto_session = boto3.Session(region_name=region)
session = sagemaker.Session(boto_session=boto_session)
role = "arn:aws:iam::538653532257:role/SagemakerExecution"  # Replace with your SageMaker execution role
bucket = "sab-ds"  # or specify your bucket


# Specify the container image for AutoGluon
container = sagemaker.image_uris.retrieve(framework="autogluon", 
                                          region=region, 
                                          version="1.0.0",
                                          image_scope='training',
                                          instance_type="ml.m5.xlarge")

# Set up SageMaker Estimator
autogluon_estimator = Estimator(
    image_uri=container,
    role=role,
    instance_count=1,
    instance_type="ml.m5.xlarge",
    output_path=f"s3://{bucket}/loan-approval",
    sagemaker_session=session,
    entry_point="train.py",
    source_dir=".",  # directory where train_autogluon.py is located
)

# Start training
autogluon_estimator.fit({"train": f"s3://{bucket}/loan-approval"})
