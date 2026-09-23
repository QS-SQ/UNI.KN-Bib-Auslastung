"""
Main File for the UNI.KN-Bib-Auslastung project
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from methods import (
    read_email, 
    preprocess_data, 
    map_router_to_location, 
    calc_occupancy, 
    save_as_csv, 
    process_serial_dfs, 
    calculate_indication,
    load_config_maps
)

load_dotenv()

### Calculate Current Occupancy for bar ###
flags = ['ok', 'not read', 'not processed', 'not mapped', 'not calculated', 'no indication']

config = load_config_maps()
if config['error']:
    flags[0] = config['error']
    
if flags[0] == 'ok':
    dfs_data, timestamps, flags[1] = read_email()

if flags[1] == 'ok':
    df_data, flags[2] = preprocess_data(dfs_data[-1])

if flags[2] == 'ok':
    df_data, flags[3] = map_router_to_location(df_data, config)
    
if flags[3] == 'ok':
    occ, flags[4] = calc_occupancy(df_data, config)


### calculate occupancy today and last week for plots ###
flags_lw = ['lw not read', 'lw not processed', 'today not processed']

start_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
end_date = (datetime.now() - timedelta(days=6)).strftime("%d-%b-%Y")
dfs_lw, timestamps_lw, flags_lw[0] = read_email(start_date, end_date)

if flags_lw[0] == 'ok':
    df_lw, flags_lw[1] = process_serial_dfs(dfs_lw, timestamps_lw, config)
    
if flags_lw[1] == 'ok':
    # remove all dfs from dfs_data that have a timestamp that is not from today
    today_str = datetime.now().strftime("%Y-%m-%d")
    dfs_data_today = [df for df, ts in zip(dfs_data, timestamps) if ts.strftime("%Y-%m-%d") == today_str]   
    timestamps_today = [ts for ts in timestamps if ts.strftime("%Y-%m-%d") == today_str] 

    # check if length of dfs_data_today and timestamps_today is the same, if not return error message
    if len(dfs_data_today) != len(timestamps_today) or len(dfs_data_today) == 0:
        flags_lw[2] = 'Error: Length of dfs_data_today and timestamps_today is zero or not the same'
    else:
        df_today, flags_lw[2] = process_serial_dfs(dfs_data_today, timestamps_today, config)
    
if flags_lw[2] == 'ok':
    save_as_csv(df_lw, os.path.join(os.getcwd(), 'docs/temp/oc_values_lw.csv'))
    save_as_csv(df_today, os.path.join(os.getcwd(), 'docs/temp/oc_values_today.csv'))


### calculate indication over last hour for arrow ###
if flags[4] == 'ok':
    occ, flags[5] = calculate_indication(df_today, occ, timestamps_today[-1], config)

if flags[5] == 'ok':
    path = os.path.join(os.getcwd(), 'docs/temp/oc_values.csv')
    save_as_csv(occ, path, timestamps[-1])


### return error messages ###
# return error message if any of the flags for todays value is not 'ok'
if any(flag != 'ok' for flag in flags):
    print('Error in processing current occupancy: ' + '; '.join([str(flag) for flag in flags if flag != 'ok']))
    
# return error message if any of the flags is not 'ok' 
if any(flag != 'ok' for flag in flags_lw):
    print('Error in processing occupancy for plots: ' + '; '.join([str(flag) for flag in flags_lw if flag != 'ok']))
