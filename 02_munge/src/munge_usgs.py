import os
import numpy as np
import pandas as pd
import re
import yaml
import utils

def get_datafile_list(read_location, s3_client=None, s3_bucket=None):
    raw_datafiles = {}
    if read_location=='S3':
        raw_datafiles = [obj['Key'] for obj in s3_client.list_objects_v2(Bucket=s3_bucket, Prefix='01_fetch/out/usgs_nwis_0')['Contents']]
    elif read_location=='local':
        prefix = os.path.join('01_fetch', 'out')
        file_prefix='usgs_nwis_0'
        raw_datafiles = [os.path.join(prefix, f) for f in os.listdir(prefix) if f.startswith(file_prefix)]
    return raw_datafiles

def param_code_to_name(df, params_df):
    for col in df.columns:
        # get 5-digit parameter code from column name
        code = col.split('_')[1]
        # find the corresponding parameter name
        full_name = params_df[params_df['parm_cd']==code]['parm_nm'].iloc[0]
        # give it a shorter machine-readable name
        name = full_name.split(',')[0].replace(' ', '_').lower()
        # rename the column
        df.rename(columns={col: name}, inplace=True)
    return df 

def read_data(raw_datafile, read_location, s3_bucket):
    if read_location == 'local':
        print(f'reading data from local: {raw_datafile}')
        # read in raw data as pandas df
        df = pd.read_csv(raw_datafile, comment='#', sep='\t', lineterminator='\n', low_memory=False)
    elif read_location == 'S3':
        print(f'reading data from s3: {raw_datafile}')
        obj = s3_client.get_object(Bucket=s3_bucket, Key=raw_datafile)
        # read in raw data as pandas df
        df = pd.read_csv(obj.get("Body"), comment='#', sep='\t', lineterminator='\n', low_memory=False)
    return df

def process_data_to_csv(raw_datafile, params_to_process, params_df, flags_to_drop, agg_level, prop_obs_required, read_location, write_location, s3_bucket, s3_client):
    '''
    process raw data text files into clean csvs, including:
        dropping unwanted flags
        converting datetime column to datetime format
        converting all data columns to numeric type
        removing metadata columns so that only datetime and data columns remain 
    '''
    # read in data file
    df = read_data(raw_datafile, read_location, s3_bucket)
    
    print(f'processing and saving locally')
    # drop first row which does not contain useful headers or data
    df.drop(index=0, inplace=True)

    # replace all flagged data we want to remove with NaN
    flag_cols = [col for col in df.columns if re.match("[0-9]+_[0-9]+_[a-z]+$", col)]
    for flag_col in flag_cols:
        flag_data_col = flag_col[:-3]
        df[flag_data_col] = np.where(df[flag_col].isin(flags_to_drop), np.nan, df[flag_data_col])

    # drop site info and flag columns
    df = df.drop(['agency_cd', 'site_no', 'tz_cd']+flag_cols, axis=1)

    # convert datetime column to datetime type
    df['datetime'] = df['datetime'].astype('datetime64')

    # make all other columns numeric
    cols = df.columns.drop('datetime')
    df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')

    # aggregate data to specified timestep
    df = utils.process_to_timestep(df, cols, agg_level, prop_obs_required)

    # drop any columns that aren't in the list we want to use
    for col in df.columns:
        if col.split('_')[1] not in params_to_process:
            df.drop(col, axis=1, inplace=True)
    
    # drop any columns with no data
    df.dropna(axis=1, how='all', inplace=True)

    # process parameter codes to names
    df = param_code_to_name(df, params_df)

    # save pre-processed data
    data_outfile_csv = os.path.join('.', '02_munge', 'out', os.path.splitext(os.path.basename(raw_datafile))[0]+'.csv')
    df.to_csv(data_outfile_csv, index=True)
    
    if write_location == 'S3':
        print('uploading to s3')
        s3_client.upload_file(data_outfile_csv, s3_bucket, '02_munge/out/'+os.path.basename(data_outfile_csv))

def main():
    # import config
    with open("02_munge/munge_config.yaml", 'r') as stream:
        config = yaml.safe_load(stream)['munge_usgs.py']

    # check where to read data inputs from
    read_location = config['read_location']

    # set up write location data outputs
    write_location = config['write_location']
    s3_client = utils.prep_write_location(write_location, config['aws_profile'])
    s3_bucket = config['s3_bucket']

    # read parameter data into df
    params_df = pd.read_csv(os.path.join('.', '01_fetch', 'out', 'metadata', 'usgs_nwis_params.csv'), dtype={"parm_cd":"string"})

    # get list of raw data files to process
    raw_datafiles = get_datafile_list(read_location, s3_client, s3_bucket)
    # determine which data flags we want to drop
    flags_to_drop = config['flags_to_drop']
    # determine which parameters we want to keep
    params_to_process = config['params_to_process']
    # number of measurements required to consider average valid
    prop_obs_required = config['prop_obs_required']
    # timestep to aggregate to
    agg_level = config['agg_level']
    # process raw data files into csv
    for raw_datafile in raw_datafiles:
        process_data_to_csv(raw_datafile, params_to_process, params_df, flags_to_drop, agg_level, prop_obs_required, read_location, write_location, s3_bucket, s3_client)

if __name__ == '__main__':
    main()
