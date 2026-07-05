"""
Method File for the UNI.KN-Bib-Auslastung project
"""

# import necessary libraries
import os
from io import BytesIO, StringIO
import pandas as pd
import imaplib
import email
from datetime import timedelta

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
    flag = 'ok'
    df_data = None
    timestamp = None
    
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
                break
            
    except Exception as e:
        flag = f'Error parsing email ID {id.decode()}: {str(e)}'
        return None, None, flag
      
    # check if a CSV attachment was found and set the flag accordingly       
    if df_data is None:
        flag = f'No CSV attachment found in email dated {timestamp}'
              
    return df_data, timestamp, flag


def read_email(start_date=None, end_date=None):
    """
    Reads the most recent email (last 24h) with a sender filter and extracts the 
    CSV attachment as a DataFrame.

    Args:
        start_date (str, optional): The start date for filtering emails in the format 'dd-MMM-YYYY'. 
            Defaults to None, which means emails from the last 24 hours will be considered.
        end_date (str, optional): The end date for filtering emails in the format 'dd-MMM-YYYY'. 
            Defaults to None, which means emails up to the current date will be considered.
        
    Returns:
        df_data (DataFrame): DataFrame containing the data from the CSV attachment.
        timestamp (str): Timestamp of the email.
        flag (str): Status flag indicating success of the email reading process.
    """
    # Connect to the email server and log in
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
    if start_date is None:
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime('%d-%b-%Y')
        
    if end_date is None:
        search_query = f'(FROM "{os.getenv("SENDER")}" SINCE {start_date})'
    else:        
        search_query = f'(FROM "{os.getenv("SENDER")}" SINCE {start_date} BEFORE {end_date})'
        
    # search for emails matching the query and get their IDs
    try:
        _, data = mail.search(None, search_query)
        mail_ids = data[0].split()
    except Exception as e:
        mail.logout()
        return None, None, f'IMAP Search failed: {e}'

    # extract csv attachment
    csvs = []
    timestamps = []
    error_flags = []
    
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
    
    else:
        mail.logout()
        return None, None, 'no email found'
 
    
def preprocess_data(df_data):
    """ 
    Preprocesses the extracted DataFrame by removing unnecessary columns 
    and summing up the Average Number of Users and Peak Number of Users 
    for each AP Name.
    
    Args:
        df_data (DataFrame): DataFrame containing the data from the CSV attachment.
    Returns:
        df_data (DataFrame): Preprocessed DataFrame with unnecessary columns removed 
            and summed up user numbers.
        flag (str): Status flag indicating success of the preprocessing process.
    """
    if df_data is None or df_data.empty:
        return df_data, 'DataFrame is empty or None'

    required_cols = ['AP Name', 'Average Number of Users', 'Peak Number of Users']
    missing_cols = [col for col in required_cols if col not in df_data.columns]
    
    if missing_cols:
        return df_data, f'Missing target operational columns: {missing_cols}'
        
    # Drop unnecessary columns
    df_data = df_data.drop(columns=['Radio Type', 'AP MACAddress'], errors='ignore')
    
    # group by 'AP Name' and sum up the 'Average Number of Users' and 'Peak Number of Users' for each AP Name
    df_cleaned = df_data.groupby('AP Name', as_index=False).agg({
        'Average Number of Users': 'sum',
        'Peak Number of Users': 'sum'
    })
        
    return df_cleaned, 'ok'


def map_router_to_location(df_data):
    """ 
    Maps the router names in the DataFrame to their corresponding locations using a CSV file 
    containing the mapping information.
    
    Args:
        df_data (DataFrame): DataFrame containing the data from the CSV attachment.
    Returns:
        df_data (DataFrame): DataFrame with an additional column for the mapped locations.
        flag (str): Status flag indicating success or failure of the mapping process.
    """
    mapping_str = os.getenv("MAPPING")
    if not mapping_str:
        return df_data, 'No mapping found in environment'
        
    try:
        router_map = pd.read_csv(StringIO(mapping_str), sep=';')
        router_map.columns = [col.strip() for col in router_map.columns]
    except Exception as e:
        return df_data, f'Error parsing MAPPING config string: {e}'
    
    # Generate mapping dictionary
    router_area_map = dict(zip(
        router_map.iloc[:, 1].astype(str).str.strip(), 
        router_map.iloc[:, 2].astype(str).str.strip()
    ))
    
    df_data['AP Name'] = df_data['AP Name'].astype(str).str.strip()
    df_data['area'] = df_data['AP Name'].map(router_area_map)

    flag = 'ok'
    
    # check if there are any AP Names that could not be mapped to a location
    unmapped_aps = df_data[df_data['area'].isna()]['AP Name'].unique()
    if len(unmapped_aps) > 0:
        flag = 'the following AP Names could not be mapped to a location: ' + ', '.join(unmapped_aps)
            
    # check if there are any AP Names in MAPPING that are not in the df_data
    missing_aps = set(router_area_map.keys()) - set(df_data['AP Name'])
    if len(missing_aps) > 0:
        flag = flag + ' and the following AP Names from the mapping file are not in the data: ' + ', '.join(missing_aps) if flag != 'ok' else 'the following AP Names from the mapping file are not in the data: ' + ', '.join(missing_aps)
            
    return df_data, flag


def calc_occupancy(df_data):
    """
    Calculate the occupancy for each location based on the average number of users 
    and the capacity defined in the capacity map.

    Args:
        df_data (DataFrame): DataFrame containing the data from the CSV attachment 
            with mapped locations.
        capacity_map (dict): Dictionary mapping locations to their capacities.
    Returns:
        occup (dict): Dictionary containing the occupancy for each location.
    """
    capacity_str = os.getenv("CAPACITY")
    if not capacity_str:
        return {}, 'No capacity data found in environment'
        
    try:
        capacity_df = pd.read_csv(StringIO(capacity_str), sep=';')
        capacity_map = dict(zip(
            capacity_df.iloc[:, 0].astype(str).str.strip(), 
            capacity_df.iloc[:, 1].astype(float)
        ))
    except Exception as e:
        return {}, f'Error processing CAPACITY string configuration: {e}'
    
    occup = {}
    flag = 'ok'
    
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
            flag = f'No data found for location {loc}, occupancy set to 0' if flag == 'ok' else flag + f'; No data found for location {loc}, occupancy set to 0'
    
    seats_str = os.getenv("SEATS")
    if not seats_str:
        return occup, 'No seats data found in environment'
    
    # calculate total occupancy as weighted average of all locations based on their seat counts
    try:
        seats_df = pd.read_csv(StringIO(seats_str), sep=';')
        seats_map = dict(zip(
            seats_df.iloc[:, 0].astype(str).str.strip(), 
            seats_df.iloc[:, 1].astype(float)
        ))
    except Exception as e:
        return occup, f'Error processing SEATS string configuration: {e}'
    
    total_seats = sum(seats_map.get(loc, 0) for loc in occup.keys())
    if total_seats > 0:
        total_occupancy = sum(occup.get(loc, 0) * seats_map.get(loc, 0) for loc in occup.keys()) / total_seats
        occup['TOTAL'] = round(total_occupancy, 2)
    else:
        occup['TOTAL'] = 0.0
        flag = 'No seat data available for any location, total occupancy set to 0' if flag == 'ok' else flag + '; No seat data available for any location, total occupancy set to 0' 
    
    # convert occupancy values to dataframe
    occ_df = pd.DataFrame(list(occup.items()), columns=['area', 'capacity']) 
    
    # add a column for the total number of seats for each location
    seats_map['TOTAL'] = sum(seats_map.values())
    occ_df['seats'] = occ_df['area'].map(seats_map).fillna(0).astype(int)
    
    return occ_df, flag


def save_as_csv(occ_df, path, time=None):
    """ 
    Save occupancy values to a csv file. 
    
    Args:
        occ_df (DataFrame): DataFrame containing the occupancy for each location.
        path (str): Path to save the csv.
        time (str): Timestamp from data.
    """
    
    # save occupancy values to a csv file
    occ_df.to_csv(path, index=False)
    
    # append timestamp to the csv file
    if time is not None:
        with open(path, 'a') as f:
            f.write(f"timestamp,{time}\n")
    
        
def process_serial_dfs(dfs, df_timestamps):
    """
    Process dataframes for serial occupancy calculation by mapping router names to locations, 
    summing up user numbers, and calculating occupancy based on capacity.
    
    Args:        
        dfs (list): List of DataFrames containing the data from the CSV attachments.
        df_timestamps (list): List of df_timestamps corresponding to each DataFrame.
        
    Returns:
        df_area (DataFrame): DataFrame containing the occupancy values for each area and time.
        flag (str): Status flag indicating success or failure of the processing.
    """
    mapping_str = os.getenv("MAPPING")
    capacity_str = os.getenv("CAPACITY")
    
    if not mapping_str or not capacity_str:
        return None, 'Configuration environment profiles missing fields'
    
    # load mapping and capacity from environment variable
    try:
        df_map = pd.read_csv(StringIO(mapping_str), sep=';')
        df_map.columns = ["description", "AP Name", "area"]
        df_map['AP Name'] = df_map['AP Name'].str.strip()
        location_map = df_map.set_index("AP Name")["area"].to_dict()

        df_capacity = pd.read_csv(StringIO(capacity_str), sep=';')
        df_capacity.columns = ["area", "factor"]
        df_capacity["area"] = df_capacity["area"].str.strip()
    except Exception as e:
        return None, f'Error parsing tracking parameters: {e}'
    
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
    
    # Merge factor metadata definitions
    df_area = pd.merge(df_area, df_capacity, on="area", how="left")
    df_area["capacity"] = round((df_area["Average Number of Users"] * df_area["factor"]).clip(upper=1.0) * 100,2)
    # remove all rows where area is 'na' and reset index
    df_area = df_area[df_area["area"] != "na"].reset_index(drop=True)
    # remove all rows with timestamp before 8am and reset index
    df_area = df_area[df_area["time"] >= "08:00"].reset_index(drop=True)
    # remove colums Average Number of Users and factor
    df_area = df_area.drop(columns=["Average Number of Users", "factor"])
    
    return df_area, 'ok'


def calculate_indication(df_data, occ, current_time):
    """
    Calculate the occupancy difference and indication for each location based on the current 
    and previous occupancy values.

    Args:
        df_data (DataFrame): DataFrame containing the occupancy values for each area and time.
        occ (DataFrame): DataFrame containing the current occupancy values for each area.
        current_time (str): Current timestamp for which the indication is being calculated.
        
    Returns:
        occ (DataFrame): Updated DataFrame containing the current occupancy values for each area with indication.
        flag (str): Status flag indicating success or failure of the indication calculation process.
    """
    
    df_diff = df_data.copy()
    
    # convert time column to datetime and only keep rows where time is between 65 and 55 minutes before current time
    df_diff['time'] = pd.to_datetime(df_diff['time'])
    df_diff = df_diff[(df_diff['time'] >= current_time - timedelta(minutes=65)) & (df_diff['time'] <= current_time - timedelta(minutes=55))]
    
    # add new column to df_diff with current time and capacity from occ DataFrame
    df_diff['time_current'] = current_time
    df_diff['capacity_current'] = df_diff['area'].map(occ.set_index('area')['capacity']).fillna(0).astype(float)
    df_diff['occupancy_diff'] = df_diff['capacity_current'] - df_diff['capacity']
    
    # calculate total occupancy difference and add it to the df_diff DataFrame
    seats_str = os.getenv('SEATS')
    if not seats_str:
        flag = 'No seats data found in environment'
        total_occupancy_diff = df_diff['occupancy_diff'].sum() / 9
    else:
        try:
            seats_df = pd.read_csv(StringIO(seats_str), sep=';')
            seats_map = dict(zip(
                seats_df.iloc[:, 0].astype(str).str.strip(), 
                seats_df.iloc[:, 1].astype(float)
            ))
        except Exception as e:
            flag = f'Error reading seats data from environment: {e}'

        # calculate total occupancy weigthed by the number of seats for each location
        df_diff['seats'] = df_diff['area'].map(seats_map).fillna(0).astype(float)
        total_occupancy_diff = (df_diff['occupancy_diff'] * df_diff['seats']).sum() / df_diff['seats'].sum()
        
    # add the total occupancy difference to the df_diff DataFrame in a new row with area 'TOTAL'
    df_diff = pd.concat([df_diff, pd.DataFrame([{
        'area': 'TOTAL',
        'time_current': current_time,
        'capacity_current': occ.set_index('area').loc['TOTAL', 'capacity'],
        'occupancy_diff': total_occupancy_diff,
        'seats': 0
    }])], ignore_index=True)
    
    # for each location check the difference and set the indication to -2, -1, 0, 1, or 2 in the occ DataFrame
    def get_indication(diff):
        if diff > 7:
            return 2
        elif diff > 2:
            return 1
        elif diff > -2:
            return 0
        elif diff > -7:
            return -1
        else:
            return -2
    
    df_diff['indication'] = df_diff['occupancy_diff'].apply(get_indication)
    
    # add the indication to the occ DataFrame
    indication_map = df_diff.set_index('area')['indication']
    occ['indication'] = occ['area'].map(indication_map).fillna(0).astype(int)
    
    return occ, 'ok'