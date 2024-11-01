import boto3
import pandas as pd
import sagemaker
from sagemaker import get_execution_role
from sagemaker import image_uris, model_uris, script_uris
import argparse

import os

from autogluon.tabular import TabularDataset, TabularPredictor



def prepare_data(path):
    data = pd.read_csv(path)
    age_bins = [20, 40, 60, 150]  # 100 as an upper bound for ages above 60
    risk_labels = ['Low Risk', 'Mid Risk', 'High Risk']
    data['risk_level'] = pd.cut(data['person_age'], bins=age_bins, labels=risk_labels)

    # Set an assumed loan term in months, e.g., 36 months
    assumed_loan_term_months = 36

    # Calculate monthly interest rate
    monthly_interest_rate = data['loan_int_rate'] / 12

    # Calculate EMI using the given formula
    emi = (data['loan_amnt'] * monthly_interest_rate * (1 + monthly_interest_rate)**assumed_loan_term_months) / \
                ((1 + monthly_interest_rate)**assumed_loan_term_months - 1)

    # Calculate Monthly DTI Ratio
    data['dti_ratio_monthly'] = emi / (data['person_income'] / 12)



    # 5. Convert 'cb_person_default_on_file' (Y/N) to binary
    data['cb_person_default_on_file'] = data['cb_person_default_on_file'].apply(lambda x: 1 if x == 'Y' else 0)

    # 6. One-hot encode 'loan_intent' and 'person_home_ownership'
    data = pd.get_dummies(data, columns=['loan_intent', 'person_home_ownership'], drop_first=True)

    # 7. Bucket loan interest rate into categories (Low, Medium, High)
    data['loan_int_rate_bucket'] = pd.cut(data['loan_int_rate'], bins=[0, 10, 15, 30], labels=['Low', 'Medium', 'High'])

    # 8. Convert 'loan_grade' (assuming it is categorical A-F) to numerical values
    loan_grade_mapping = {'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 1, 'F': 0}
    data['loan_grade'] = data['loan_grade'].map(loan_grade_mapping)
    return data
if __name__ == '__main__':
     parser = argparse.ArgumentParser()

    # SageMaker specific arguments: input/output directories
     parser.add_argument('--train', type=str, default=os.environ['SM_CHANNEL_TRAIN'])
     parser.add_argument('--model-dir', type=str, default=os.environ['SM_MODEL_DIR'])
     args = parser.parse_args()

     train_data = prepare_data(os.path.join(args.train, 'train.csv'))
     train_data = TabularDataset(train_data)
     train_data = train_data.drop(columns=['id'])

    # Set target label for prediction
     label = 'loan_status'
    
    # Create a TabularPredictor instance and train the model
     predictor = TabularPredictor(label=label, eval_metric='roc_auc')
     predictor.fit(train_data)

    # Save the trained model to the output path provided by SageMaker
     predictor_path = os.path.join(args.model_dir, 'loan_approval_model')
     predictor.save(predictor_path)
    