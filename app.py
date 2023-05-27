from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
import numpy as np
from datetime import datetime
import os
import math
import warnings

warnings.filterwarnings('ignore', category=Warning)

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'

# Function to handle file cleanup after processing
def cleanup_files():
    files_to_delete = ['sales.csv', 'pricing.csv', 'repo.csv']
    for file_name in files_to_delete:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_name)
        if os.path.exists(file_path):
            os.remove(file_path)

@app.route('/', methods=['GET', 'POST'])
def upload_files():
    if request.method == 'POST':
        # Get the uploaded files
        sales_file = request.files['sales_file']
        pricing_file = request.files['pricing_file']
        repo_file = request.files['repo_file']

        # Save the uploaded files
        sales_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'sales.csv'))
        pricing_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'pricing.csv'))
        repo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'repo.csv'))

        # Process the files
        process_files()

        # Clean up the uploaded files
        cleanup_files()

        return redirect(url_for('download'))

    return render_template('upload.html')

def process_files():
    # Read the uploaded files
    Sales_File = pd.read_csv('uploads/sales.csv')
    Pricing_File = pd.read_csv('uploads/pricing.csv')
    Repo_File = pd.read_csv('uploads/repo.csv')

    # REMOVE TRAILING SPACES FROM COLUMN NAMES
    # List of DataFrames
    dataframes = [Sales_File, Pricing_File, Repo_File]
    # Strip spaces from column names for each DataFrame
    for df in dataframes:
        df.rename(columns=lambda x: x.strip(), inplace=True)
    
    # CHANGE DATE FORMAT
    def convert_date_format(date_string):
        # Specify the input date format
        input_format = "%d %b, %Y"  # Example: '01 Apr, 2023'
        # Convert the date string to a datetime object
        original_date = datetime.strptime(date_string, input_format)
        # Extract day, month, and year components
        day = str(original_date.day)
        month = original_date.strftime("%b").lower()
        year = str(original_date.year)
        # Construct the desired date format
        desired_format = f"{day}/{month}/{year}"  # Example: '1/apr/2023'
        return desired_format
    # Apply the function to the 'Date' column
    Sales_File['Sale Day'] = Sales_File['Sale Day'].apply(convert_date_format)

    # CLEAN DATA AND SELECT THE NEEDED COLUMNS
    # Cleaning Sales File to get Sales Data (Manual)
    Sales_Filter = Sales_File[(Sales_File['Product Source'] == 'formulary') & (Sales_File['Is Manual'] == True)]
    Sales_Data = Sales_Filter[["Sale Day", "Sale Facility", "Mph Arma ID", 
                "Customer Type", "Vdl Drug ID", "Vdl Drug Display Name", 
                "Sale Item Selling Price Local", "Product Source", "Is Manual"]]
    Sales_Data['Vdl Drug ID'] = Sales_Data['Vdl Drug ID'].astype('int64')
    # Cleaning Pricing File to get Pricing Data
    Pricing_Data = Pricing_File[["mPharma Drug Name", "Drug ID.1", "Pack Size",
                            "Approved Selling Price (Mar 2023) Unit",
                            "QRx Mutti Unit Price", "QRx Thea Unit Price"]]
    Pricing_Data['Drug ID.1'] = Pricing_Data['Drug ID.1'].str.lstrip("NG-")
    Pricing_Data.rename(columns={'Drug ID.1': 'Product ID'}, inplace=True)
    Pricing_Data["Product ID"] = Pricing_Data["Product ID"].astype(int)

    # CREATE CUSTOMER TYPE COLUMN
    Sales_Data["Sale Price Category"] = np.where(Sales_Data["Customer Type"] == "guest", "QRx Thea Unit Price", "QRx Mutti Unit Price")

    # MERGE THE SALES AND REPO FILE USING A LEFT JOIN**
    Sales_Repo = pd.merge(Sales_Data, Repo_File, left_on='Vdl Drug ID', right_on='Old', how='left')
    Sales_Repo.loc[Sales_Repo['New'].notnull(), 'Vdl Drug ID'] = Sales_Repo['New']
    Sales_Repo = Sales_Repo.drop(columns=['Product Name','Old', 'New'])
    Sales_Repo.rename(columns={'Vdl Drug ID': 'Product ID'}, inplace=True)

    # MERGE SALES_REPO WITH PRICING FILE
    Sales_Price_Data = pd.merge(Sales_Repo, Pricing_Data[["Product ID","Approved Selling Price (Mar 2023) Unit"]], on=["Product ID"], how="left")
    Sales_Price_Data.loc[:, "Product ID"] = Sales_Price_Data["Product ID"].astype(int)

    # APPENDING PRICE TO SALE PRICE CATEGORY
    def price(row):
        if row['Sale Price Category'] in Pricing_Data.columns:
            match = Pricing_Data[Pricing_Data["Product ID"] == row["Product ID"]]
            if not match.empty:
                return match.iloc[0][row['Sale Price Category']]
            else:
                return np.nan
        else:
            return np.nan

    Sales_Price_Data["Unit Sale Price Local"] = Sales_Price_Data.apply(price, axis=1)

    # CLEANING SALES DATA COLUMNS
    Sales_Price_Data['Unit Sale Price Local'] = Sales_Price_Data['Unit Sale Price Local'].str.replace(',', '')
    Sales_Price_Data['Unit Sale Price Local'] = pd.to_numeric(Sales_Price_Data['Unit Sale Price Local'])

    # CALCULATING QUANTITY SOLD(UNITS)
    Sales_Price_Data["Quantity in Units"] = Sales_Price_Data["Sale Item Selling Price Local"] / Sales_Price_Data["Unit Sale Price Local"]
    Sales_Price_Data["Quantity in Units"] = Sales_Price_Data["Quantity in Units"].apply(np.floor)
    Sales_Price_Data.loc[Sales_Price_Data['Quantity in Units'] < 1, 'Quantity in Units'] = 1

    # CALCULATE NEW UNIT SELLING PRICE
    Sales_Price_Data['New Unit Selling Price'] = round((Sales_Price_Data['Sale Item Selling Price Local'] / Sales_Price_Data['Quantity in Units']),2)

    # GET UNIT COST OF SALES FROM UNIT VMI COST PRICE
    Sales_Price_Data.rename(columns={'Approved Selling Price (Mar 2023) Unit': 'Unit VMI Cost Price'}, inplace=True)
    Sales_Price_Data['Unit VMI Cost Price'] = Sales_Price_Data['Unit VMI Cost Price'].replace(',', '', regex=True)
    Sales_Price_Data['Unit VMI Cost Price'] = pd.to_numeric(Sales_Price_Data['Unit VMI Cost Price'])
    Sales_Price_Data['VMI Cost of Sales'] = Sales_Price_Data['Unit VMI Cost Price'] * Sales_Price_Data['Quantity in Units']

    # CALCULATE MARGIN
    Sales_Price_Data['Margin'] = Sales_Price_Data['Sale Item Selling Price Local'] - Sales_Price_Data['VMI Cost of Sales']

    # UPDATE TYPE OF SALE (CUSTOM SALES)
    Sales_Price_Data.loc[:, 'Type of Sale'] = 'Custom Sale'

    # CREATE CUSTOM SALES DATA FROM SALES DATA
    Custom_Sales_Data = Sales_Price_Data[["Sale Day", "Sale Facility", "Mph Arma ID", "Product ID", "Vdl Drug Display Name", 
                                        "New Unit Selling Price", "Quantity in Units", "Sale Item Selling Price Local", 
                                        "Unit VMI Cost Price", "VMI Cost of Sales", "Margin", "Type of Sale"]]

    # CHECK FOR NULL VALUES IN NEW UNIT SELLING PRICE
    Custom_Sales_Data[Custom_Sales_Data['New Unit Selling Price'].isna()]

    # CREATE POS AND WRANGLE DATA
    # Filter Product Source and Is Manual
    POS = Sales_File[(Sales_File['Product Source'] == 'formulary') & (Sales_File['Is Manual'] == False)]
    # Select Needed Columns
    POS = POS[["Sale Day", "Sale Facility", "Mph Arma ID", "Customer Type", "Vdl Drug ID", "Vdl Drug Display Name", 
            "Unit Selling Price Local","Quantity In Units", "Sale Item Selling Price Local", "Unit Vm I Cost Price Local", 
            "Sale Item Vm I Cost Price Local", "Sale Item Vm I Margin Local", "Product Source", "Is Manual"]]
    # Rename columns in the POS DataFrame
    POS.rename(columns={'Vdl Drug ID': 'Product ID',
                        'Vdl Drug Display Name':'Product name',
                        'Unit Vm I Cost Price Local': 'Unit VMI Cost Price',
                        'Unit Selling Price Local': 'New Unit Selling Price',
                        'Sale Item Vm I Cost Price Local': 'VMI Cost of Sales',
                        'Sale Item Vm I Margin Local': 'Margin',
                        'Quantity In Units': 'Quantity in Units'}, inplace=True)

    # UPDATE TYPE OF SALES (POS SALES)
    POS.loc[:, 'Type of Sale'] = 'POS Sale'

    # CONCATENATE CUSTOME SALES DATA AND POS
    Final_Sales_Data = pd.concat([Custom_Sales_Data, POS])
    # Convert 'Margin' column to numeric type
    Final_Sales_Data['Margin'] = pd.to_numeric(Final_Sales_Data['Margin'], errors='coerce')
    # Filter rows where 'Margin' is greater than or equal to 0
    Final_Sales_Data = Final_Sales_Data[Final_Sales_Data['Margin'] >= 0]

    # REARRANGE DATA BY SALE DAY
    Final_Sales_Data = Final_Sales_Data.sort_values(by = 'Sale Day')
    Final_Sales_Data.head()

    # CREATE TAX AND 'IS MUTTI SALE' COLUMNS
    Final_Sales_Data['Tax'] = pd.Series(dtype = 'int64')
    Final_Sales_Data['Tax'] = ' '
    Final_Sales_Data['Is Mutti Sale'] = pd.Series(dtype = 'int64')
    Final_Sales_Data['Is Mutti Sale'] = ' '
    Final_Sales_Data
    
    # Save the processed data to a CSV file
    Final_Sales_Data.to_csv('processed_data.csv', index=False)

   
@app.route('/download', methods=['GET', 'POST'])
def download():
    if request.method == 'POST':
        # Logic for preparing the download page goes here
        return send_file('processed_data.csv', as_attachment=True)
    
    return render_template('download.html')

if __name__ == '__main__':
    app.run(debug=True)