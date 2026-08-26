"""
Method File for the UNI.KN-Bib-Auslastung project
"""

import os
import email
import imaplib
from io import BytesIO, StringIO
from datetime import timedelta
import pandas as pd


def load_config_maps():
    """ 
    Loads configuration maps from environment variables and returns a dictionary with mapping, 
    capacity, and seats information.
    
    Returns:
        config (dict): Dictionary containing 'router_map', 'capacity', 'seats', and 'error' keys.
    """
    config = {'mapping': None, 'capacity': {}, 'seats': {}, 'error': None}
    
    mapping_str = os.getenv("MAPPING")
    capacity_str = os.getenv("CAPACITY")
    seats_str = os.getenv("SEATS")
    
    if not mapping_str or not capacity_str or not seats_str:
        config['error'] = 'Missing configuration (MAPPING and/or CAPACITY and/or SEATS) in environment'
        return config

    try:
        # Load MAPPING
        map_df = pd.read_csv(StringIO(mapping_str), sep=';')
        map_df.columns = [col.strip() for col in map_df.columns]
        config['router_map'] = dict(zip(
            map_df.iloc[:, 1].astype(str).str.strip(), 
            map_df.iloc[:, 2].astype(str).str.strip()
        ))

        # Load CAPACITY
        cap_df = pd.read_csv(StringIO(capacity_str), sep=';')
        cap_df.columns = [col.strip() for col in cap_df.columns]
        config['capacity'] = dict(zip(
            cap_df.iloc[:, 0].astype(str).str.strip(), 
            cap_df.iloc[:, 1].astype(float)
        ))

        # Load SEATS
        if seats_str:
            seats_df = pd.read_csv(StringIO(seats_str), sep=';')
            seats_df.columns = [col.strip() for col in seats_df.columns]
            config['seats'] = dict(zip(
                seats_df.iloc[:, 0].astype(str).str.strip(), 
                seats_df.iloc[:, 1].astype(float)
            ))
    except Exception as e:
        config['error'] = f'Error parsing configuration environment string: {e}'

    return config


def extract_csv_attachment(mail, id):
    """
    Extracts the CSV attachment from an email message.

    Args:
        mail (IMAP4): An instance of the IMAP4 class representing the email connection.
        id (bytes): The ID of the email message to extract the attachment from.
    Returns:
        df_data (DataFrame): DataFrame containing the data from the CSV attachment.
        timestamp (str): Timestamp of the email.
        flag (str): Status flag indicating success of the attachment extraction process.
    """
    # fetch the email message by ID and parse it into an email object
    try:
        _, msg_data = mail.fetch(id, "(RFC822)")
        raw_msg = msg_data[0][1]
        msg = email.message_from_bytes(raw_msg)
        
        # extract timestamp
        if msg['Date']:
            timestamp = pd.to_datetime(msg['Date']).tz_localize(None).replace(second=0)
        else:
            timestamp = pd.Timestamp.now().replace(second=0)

        # loop through the email parts to find the CSV attachment and read it into a DataFrame
        for part in msg.walk():
            filename = part.get_filename()
            if filename and filename.lower().endswith('.csv'):
                payload = part.get_payload(decode=True)
                df_data = pd.read_csv(BytesIO(payload), delimiter=',', skiprows=8, on_bad_lines='skip')
                return df_data, timestamp, 'ok'
        # if no CSV attachment was found, return an error message
        return None, timestamp, f'No CSV attachment found in email dated {timestamp}'
    
    # handle exceptions that occur during the extraction process and return an error message
    except Exception as e:
        return None, None, f'Error parsing email ID {id.decode()}: {str(e)}'


def read_email(start_date=None, end_date=None):
    """
    Reads emails from the configured email server and extracts CSV attachments.
    
    Args:
        start_date (str, optional): The start date for email search in 'dd-MMM-yyyy' format. Defaults to None (1 day ago).
        end_date (str, optional): The end date for email search in 'dd-MMM-yyyy' format. Defaults to None (today).
        
    Returns:
        csvs (list): List of DataFrames containing the data from the CSV attachments.
        timestamps (list): List of timestamps corresponding to each email.
        flag (str): Status flag indicating success of the email reading process.
    """
    # Try to connect to the email server and log in
    try:
        server = os.environ.get("SERVER")
        port = int(os.environ.get('PORT'))

        if port == 993:
            mail = imaplib.IMAP4_SSL(server, port)
        else:
            mail = imaplib.IMAP4(server, port)
            mail.starttls()
        mail.login(os.getenv("USER"), os.getenv("PASSWORD"))
        mail.select("INBOX")
        
    except Exception as e:
        return None, None, f'Error connecting to email server, {e}'

    # create search query
    start_date = start_date or (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime('%d-%b-%Y')
    search_query = f'(FROM "{os.getenv("SENDER")}" SINCE {start_date})' if end_date is None else f'(FROM "{os.getenv("SENDER")}" SINCE {start_date} BEFORE {end_date})'
    
    # search for emails matching the query and get their IDs 
    try:
        _, data = mail.search(None, search_query)
        mail_ids = data[0].split()
    except Exception as e:
        mail.logout()
        return None, None, f'IMAP Search failed: {e}'

    # extract csv attachments and timestamps from the emails, and return them along with a status flag
    csvs, timestamps, error_flags = [], [], []
    if mail_ids:
        for mail_id in mail_ids:
            df_data, timestamp, wf = extract_csv_attachment(mail, mail_id)
            if wf == 'ok':
                csvs.append(df_data)
                timestamps.append(timestamp)
            else:
                error_flags.append(wf)
        mail.logout()
        
        combined_flag = 'ok' if not error_flags else '; '.join(error_flags)
        return (csvs, timestamps, 'ok') if csvs else (None, None, combined_flag)
    
    mail.logout()
    return None, None, 'no email found'


def preprocess_data(df_data):
    """
    Preprocesses the DataFrame by cleaning and aggregating the data.
    
    Args:
        df_data (DataFrame): The input DataFrame to preprocess.
        
    Returns:
        df_cleaned (DataFrame): The cleaned and aggregated DataFrame.
        flag (str): Status flag indicating success of the preprocessing step.
    """
    # Check if the DataFrame is empty or None
    if df_data is None or df_data.empty:
        return df_data, 'DataFrame is empty or None'

    # Check for required columns in the DataFrame
    required_cols = ['AP Name', 'Average Number of Users', 'Peak Number of Users']
    missing_cols = [col for col in required_cols if col not in df_data.columns]
    
    if missing_cols:
        return df_data, f'Missing target operational columns: {missing_cols}'
    
    # clean and aggregate the data by dropping unnecessary columns and summing user counts per AP Name 
    df_cleaned = df_data.drop(columns=['Radio Type', 'AP MACAddress'], errors='ignore')
    df_cleaned = df_cleaned.groupby('AP Name', as_index=False).agg({
        'Average Number of Users': 'sum',
        'Peak Number of Users': 'sum'
    })
        
    return df_cleaned, 'ok'


def map_router_to_location(df_data, config):
    """
    Maps router names to their corresponding locations using the configuration mapping.
    
    Args:
        df_data (DataFrame): The input DataFrame containing router names.
        config (dict): Configuration dictionary containing the router mapping.
        
    Returns:
        df_data (DataFrame): The DataFrame with an added 'area' column for locations.
        flag (str): Status flag indicating success of the mapping step.
    """
    router_area_map = config['router_map']
    df_data['AP Name'] = df_data['AP Name'].astype(str).str.strip()
    df_data['area'] = df_data['AP Name'].map(router_area_map)

    flag_parts = []
    
    # Check for unmapped APs in incoming data
    unmapped_aps = df_data[df_data['area'].isna()]['AP Name'].unique()
    if len(unmapped_aps) > 0:
        flag_parts.append('the following AP Names could not be mapped to a location: ' + ', '.join(unmapped_aps))
            
    # Check for APs in MAPPING file missing from incoming data
    missing_aps = set(router_area_map.keys()) - set(df_data['AP Name'])
    if len(missing_aps) > 0:
        flag_parts.append('the following AP Names from the mapping file are not in the data: ' + ', '.join(missing_aps))
            
    flag = ' and '.join(flag_parts) if flag_parts else 'ok'
    return df_data, flag


def calc_occupancy(df_data, config):
    """
    Calculates occupancy percentages for each location based on the provided DataFrame and configuration.
    
    Args:
        df_data (DataFrame): The input DataFrame containing location data.
        config (dict): Configuration dictionary containing capacity and seats information.
        
    Returns:
        occ_df (DataFrame): DataFrame containing occupancy percentages and seat counts for each location.
        flag (str): Status flag indicating success of the occupancy calculation step.
    """
    capacity_map = config['capacity']
    seats_map = config['seats']
    
    occup = {}
    flag_parts = []
    
    for loc, capacity in capacity_map.items():
        if loc in ['nf', 'na']:
            continue
            
        loc_data = df_data[df_data['area'] == loc]
        if not loc_data.empty:
            avg_users = loc_data['Average Number of Users'].sum()
            occupancy = min(avg_users * capacity, 1.0) * 100
            occup[loc] = round(occupancy, 2)
        else:
            occup[loc] = 0.0
            flag_parts.append(f'No data found for location {loc}, occupancy set to 0')
    
    if seats_map == {}:
        occ_df = pd.DataFrame(list(occup.items()), columns=['area', 'capacity'])
        occ_df['seats'] = 0
        return occ_df, 'No seats data found in environment'
    
    total_seats = sum(seats_map.get(loc, 0) for loc in occup.keys())
    if total_seats > 0:
        total_occupancy = sum(occup.get(loc, 0) * seats_map.get(loc, 0) for loc in occup.keys()) / total_seats
        occup['TOTAL'] = round(total_occupancy, 2)
    else:
        occup['TOTAL'] = 0.0
        flag_parts.append('No seat data available for any location, total occupancy set to 0')
    
    # convert occupancy values to dataframe
    occ_df = pd.DataFrame(list(occup.items()), columns=['area', 'capacity']) 
    
    # add a column for the total number of seats for each location
    seats_map['TOTAL'] = sum(seats_map.values())
    occ_df['seats'] = occ_df['area'].map(seats_map).fillna(0).astype(int)
    
    flag = 'ok' if not flag_parts else '; '.join(flag_parts)
    return occ_df, flag


def save_as_csv(df, path, time_stamp=None):
    """
    Saves a DataFrame to a CSV file at the specified path, creating directories if necessary.
    
    Args:
        df (DataFrame): The DataFrame to save.
        path (str): The file path where the CSV will be saved.
        time_stamp (str, optional): An optional timestamp to append to the CSV file. Defaults to None.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    
    # append timestamp to the csv file
    if time_stamp is not None:
        with open(path, 'a') as f:
            f.write(f"timestamp,{time_stamp}\n")


def process_serial_dfs(dfs, df_timestamps, config):
    """
    Processes a list of DataFrames by mapping router names to locations and calculating 
    occupancy percentages.
    
    Args:
        dfs (list): List of DataFrames to process.
        df_timestamps (list): List of timestamps corresponding to each DataFrame.
        config (dict): Configuration dictionary containing mapping, capacity, and seats information.
        
    Returns:
        df_area (DataFrame): DataFrame containing occupancy percentages for each area and time.
        flag (str): Status flag indicating success of the processing step.
    """

    location_map = config['router_map']
    capacity_map = config['capacity']
    
    processed_dfs = []
    for i, df in enumerate(dfs):
        if df is not None and not df.empty:
            df_copy = df.copy()
            df_copy["AP Name"] = df_copy["AP Name"].astype(str).str.strip()
            df_copy["area"] = df_copy["AP Name"].map(location_map)
            
            # Add timestamps to the DataFrame
            t_val = df_timestamps[i]
            df_copy["time"] = t_val.strftime("%H:%M") if isinstance(t_val, pd.Timestamp) else str(t_val)[:5]
            
            # Keep only operational slices
            df_copy = df_copy[["area", "time", "Average Number of Users"]]
            processed_dfs.append(df_copy)
            
    if not processed_dfs:
        return None, 'No clean target historical records available'
    
    # add capacity information and calculate occupancy        
    combined_df = pd.concat(processed_dfs, ignore_index=True)
    df_area = combined_df.groupby(["area", "time"], as_index=False)["Average Number of Users"].sum()
    
    df_area["factor"] = df_area["area"].map(capacity_map)
    df_area["capacity"] = round((df_area["Average Number of Users"] * df_area["factor"]).clip(upper=1.0) * 100, 2)
    
    df_area = df_area[(df_area["area"] != "na") & (df_area["time"] >= "08:00")].reset_index(drop=True)
    df_area = df_area.drop(columns=["Average Number of Users", "factor"])
    
    return df_area, 'ok'


def calculate_indication(df_data, occ, current_time, config):
    """
    Calculates the occupancy indication for each area based on the difference in occupancy
    between the current time and the same time one hour ago.
    
    Args:
        df_data (DataFrame): The DataFrame containing occupancy data for the current time.
        occ (DataFrame): The DataFrame containing occupancy data for the previous time.
        current_time (datetime): The current timestamp for which the indication is calculated.
        config (dict): Configuration dictionary containing mapping, capacity, and seats information.
        
    Returns:
        occ (DataFrame): Updated DataFrame with the calculated indication for each area.
        flag (str): Status flag indicating success of the indication calculation step.
    """
    flag = 'ok'
    df_diff = df_data.copy()
    
    # Extract the current date prefix (e.g., "2026-08-26 ")
    date_prefix = current_time.strftime("%Y-%m-%d ")
    # Combine date with HH:MM time string and parse using explicit format
    df_diff['time'] = pd.to_datetime(date_prefix + df_diff['time'], format="%Y-%m-%d %H:%M")
    # Filter for values between 65 and 55 minutes ago
    df_diff = df_diff[(df_diff['time'] >= current_time - timedelta(minutes=65)) & (df_diff['time'] <= current_time - timedelta(minutes=55))]
    
    if df_diff.empty:
        df_diff = df_data[df_data['time'] == '08:00']

    # add new column to df_diff with current time and capacity from occ DataFrame   
    df_diff['time_current'] = current_time
    df_diff['capacity_current'] = df_diff['area'].map(occ.set_index('area')['capacity']).fillna(0).astype(float)
    df_diff['occupancy_diff'] = df_diff['capacity_current'] - df_diff['capacity']
    
    seats_map = config['seats']
    
    if not seats_map:
        flag = 'No seats data found in environment'
        total_occupancy_diff = df_diff['occupancy_diff'].sum() / 9
    else:
        df_diff['seats'] = df_diff['area'].map(seats_map).fillna(0).astype(float)
        total_occupancy_diff = (df_diff['occupancy_diff'] * df_diff['seats']).sum() / df_diff['seats'].sum()
        
    df_diff = pd.concat([df_diff, pd.DataFrame([{
        'area': 'TOTAL',
        'time_current': current_time,
        'capacity_current': occ.set_index('area').loc['TOTAL', 'capacity'],
        'occupancy_diff': total_occupancy_diff,
        'seats': 0
    }])], ignore_index=True)
    
    # for each location check the difference and set the indication to -2, -1, 0, 1, or 2 in the occ DataFrame
    def get_indication(diff):
        if diff > 7.5:
            return 2
        elif diff > 2.5:
            return 1
        elif diff > -2.5:
            return 0
        elif diff > -7.5:
            return -1
        else:
            return -2
    
    # add the indication to the occ DataFrame
    df_diff['indication'] = df_diff['occupancy_diff'].apply(get_indication)
    occ['indication'] = occ['area'].map(df_diff.set_index('area')['indication']).fillna(0).astype(int)
    
    return occ, flag