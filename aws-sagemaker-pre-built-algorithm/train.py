import boto3
import pandas as pd
import sagemaker
from sagemaker import get_execution_role
from sagemaker import image_uris, model_uris, script_uris
def init_sagemaker():
    # initialize sagemaker session
    aws_role = get_execution_role()
    aws_region = boto3.Session().region_name
    sess = sagemaker.Session()


def get_data(source):
    # download data from s3 and give back a pandas dataframe
    s3 = boto3.client('s3')
    obj = s3.get_object(Bucket='sab-ds',Key=source)
    data = pd.read_csv(obj['Body'])
    return data
def feature_engineering(data):
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
    data = get_data('loan-approval/test.csv')
    data = feature_engineering(data)
    