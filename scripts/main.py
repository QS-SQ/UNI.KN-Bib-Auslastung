"""
Main File for the UNI.KN-Bib-Auslastung project used in the action workflow
"""

# import necessary libraries
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from methods import read_email, preprocess_data, map_router_to_location, calc_occupancy, save_as_csv, process_serial_dfs

# only for local execution
load_dotenv()

### Calculate Current Occupancy ###
flags = ['not read', 'not processed', 'not mapped', 'not calculated']

dfs_data, timestamps, flags[0] = read_email()

if flags[0] == 'ok':
    df_data, flags[1] = preprocess_data(dfs_data[-1])

if flags[1] == 'ok':
    df_data, flags[2] = map_router_to_location(df_data)
    
if flags[2] != 'No mapping found in environment':
    occ, flags[3] = calc_occupancy(df_data)
    
if flags[3] == 'ok':
    path = os.path.join(os.getcwd(), 'docs/temp/oc_values.csv')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_as_csv(occ, path, timestamps[-1])

# return error message if any of the flags is not 'ok'
if any(flag != 'ok' for flag in flags):
    error_message = 'Error in processing current occupancy: ' + '; '.join([str(flag) for flag in flags if flag != 'ok'])
    print(error_message)
 
   
### calculate occupancy today and last week ###
flags_lw = ['not read', 'lw not processed', 'today not processed']

start_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
end_date = (datetime.now() - timedelta(days=6)).strftime("%d-%b-%Y")
dfs_lw, timestamps_lw, flags_lw[0] = read_email(start_date, end_date)

if flags_lw[0] == 'ok':
    df_lw, flags_lw[1]= process_serial_dfs(dfs_lw, timestamps_lw)
if flags_lw[1] == 'ok':
    # remove all dfs from dfs_data that have a timestamp that is not from today
    dfs_data_today = [df for df, ts in zip(dfs_data, timestamps) if ts.strftime("%Y-%m-%d") == datetime.now().strftime("%Y-%m-%d")]   
    timestamps_today = [ts for ts in timestamps if ts.strftime("%Y-%m-%d") == datetime.now().strftime("%Y-%m-%d")] 
    # check if length of dfs_data_today and timestamps_today is the same, if not return error message
    if len(dfs_data_today) != len(timestamps_today) and len(dfs_data_today) > 0:
        flags_lw[2] = 'Error: Length of dfs_data_today and timestamps_today is zero or not the same'
    else:
        df_today, flags_lw[2] = process_serial_dfs(dfs_data_today, timestamps_today)
    
if flags_lw[2] == 'ok':
    path_lw = os.path.join(os.getcwd(), 'docs/temp/oc_values_lw.csv')
    os.makedirs(os.path.dirname(path_lw), exist_ok=True)
    df_lw.to_csv(path_lw, index=False)
    path_today = os.path.join(os.getcwd(), 'docs/temp/oc_values_today.csv')
    os.makedirs(os.path.dirname(path_today), exist_ok=True)
    df_today.to_csv(path_today, index=False)

# return error message if any of the flags is not 'ok' 
if any(flag != 'ok' for flag in flags_lw):
    error_message = 'Error in processing occupancy for plots: ' + '; '.join([str(flag) for flag in flags_lw if flag != 'ok'])
    print(error_message)