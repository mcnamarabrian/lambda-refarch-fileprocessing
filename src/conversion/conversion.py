import json
import logging
import os
import sys

import aws_lambda_logging
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
import boto3
import botocore
import markdown
import tempfile

patch_all()


max_object_size = 104857600  # 100MB = 104857600 bytes

aws_region = os.environ['AWS_REGION']

conversion_queue = os.getenv('CONVERSION_QUEUE')

target_bucket = os.getenv('TARGET_BUCKET')

log_level = os.getenv('LOG_LEVEL')

s3_resource = boto3.resource('s3', region_name=aws_region)

sqs_client = boto3.client('sqs', region_name=aws_region)

log = logging.getLogger()


def check_s3_object_size(bucket, key_name):
    """Take in a bucket and key and return the number of bytes

    Parameters
    ----------
    bucket: string, required
        Bucket name where object is stored

    key_name: string, required
        Key name of object

    Returns
    -------
    size: integer
        Size of key_name in bucket
    """

    xray_recorder.begin_subsegment('## get_object_size')
    subsegment = xray_recorder.current_subsegment()
    subsegment.put_metadata('object', f's3://{bucket}/{key_name}')

    try:
        size = s3_resource.Object(bucket, key_name).content_length
        subsegment.put_metadata('object_size', size)
    except Exception as e:
        log.error(f'Error: {str(e)}')
        size = 'NaN'
        subsegment.put_metadata('object_size', size)

    xray_recorder.end_subsegment()
    return(size)


def get_s3_object(bucket, key_name, local_file):
    """Download object in S3 to local file

    Parameters
    ----------
    bucket: string, required
        Bucket name where object is stored

    key_name: string, required
        Key name of object

    local_file: string, required

    Returns
    -------
    result: string
        Result of operation ('ok' or exception)
    """

    xray_recorder.begin_subsegment('## get_object_to_convert')
    subsegment = xray_recorder.current_subsegment()
    subsegment.put_metadata('object', f's3://{bucket}/{key_name}')

    try:
        s3_resource.Bucket(bucket).download_file(key_name, local_file)
        result = 'ok'
        subsegment.put_annotation('OBJECT_DOWNLOAD', 'SUCCESS')
    except botocore.exceptions.ClientError as e:
        subsegment.put_annotation('OBJECT_DOWNLOAD', 'FAILURE')
        if e.response['Error']['Code'] == '404':
            result = f'Error: s3://{bucket}/{key_name} does not exist'
        else:
            result = f'Error: {str(e)}'

    xray_recorder.end_subsegment()
    return(result)


def convert_to_html(file):
    """Convert Markdown in file to HTML

    Parameters
    ----------
    file: string, required
        Local file to be converted

    Returns
    -------
    string
        Resulting HTML5
    """

    xray_recorder.begin_subsegment('## convert_to_html')
    subsegment = xray_recorder.current_subsegment()
    subsegment.put_metadata('file', file)

    try:
        file_open = open(file, 'r')
        file_string = file_open.read()
        subsegment.put_annotation('MARKDOWN_CONVERSION', 'SUCCESS')
        xray_recorder.end_subsegment()
        file_open.close()
        return(markdown.markdown(file_string))
    except Exception as e:
        log.error(f'Could not open or read {file}: {str(e)}')
        subsegment.put_annotation('MARKDOWN_CONVERSION', 'FAILURE')
        xray_recorder.end_subsegment()
        raise


def upload_html(bucket, key, source_file):
    """Upload local file to S3 bucket

    Parameters
    ----------
    target_bucket: string, required
        S3 bucket where object is stored

    key_name: string, required
        Key name of object

    source_file: string, required
        Name of local file

    Returns
    -------
    result: string
        Result of operation ('ok' or exception)
    """

    xray_recorder.begin_subsegment('## upload_html_to_s3')
    subsegment = xray_recorder.current_subsegment()
    subsegment.put_metadata('object', f's3://{bucket}/{key}')

    try:
        s3_resource.Object(bucket, key).upload_file(source_file)
        result = 'ok'
        subsegment.put_annotation('OBJECT_UPLOAD', 'SUCCESS')
    except Exception as e:
        subsegment.put_annotation('OBJECT_UPLOAD', 'FAILURE')
        log.error(f'Could not upload {source_file} to {bucket}: {str(e)}')
        result = 'fail'

    xray_recorder.end_subsegment()
    return(result)


def handler(event, context):
    aws_lambda_logging.setup(level=log_level,
                             aws_request_id=context.aws_request_id)

    for record in event['Records']:
        tmpdir = tempfile.mkdtemp()

        sqs_receipt_handle = record['receiptHandle']

        try:
            json_body = json.loads(record['body'])

            for record in json_body['Records']:
                bucket_name = record['s3']['bucket']['name']
                key_name = record['s3']['object']['key']

                size = check_s3_object_size(bucket_name, key_name)

                if size >= max_object_size:
                    error_message = f'Source S3 object '
                    error_message = f's3://{bucket_name}/{key_name} is larger '
                    error_message += f'than {max_object_size} '
                    error_message += f'(max object bytes)'
                    log.error(error_message)
                    raise Exception('Source S3 object too large')

                local_file = os.path.join(tmpdir, key_name)

                download_status = get_s3_object(bucket_name,
                                                key_name,
                                                local_file)

                if download_status == 'ok':
                    success_message = f'Success: Download to '
                    success_message += f'{local_file} for conversion'
                    log.info(success_message)
                else:
                    log.error(f'Fail to put object to {local_file}')
                    raise Exception(f'Fail to put object to {local_file}')

                html = convert_to_html(local_file)

                html_filename = os.path.splitext(key_name)[0] + '.html'

                local_html_file = os.path.join(tmpdir, html_filename)

                with open(local_html_file, 'w') as outfile:
                    outfile.write(html)
                    log.info(f'''Success: Converted s3://{bucket_name}/{key_name}
                        to {local_html_file}''')
                outfile.close()

                html_upload = upload_html(
                            target_bucket,
                            html_filename,
                            local_html_file)

                if html_upload == 'ok':
                    '''If function could put the converted file to the S3 bucket then
                    remove message from the SQS queue'''
                    try:
                        sqs_client.delete_message(
                            QueueUrl=conversion_queue,
                            ReceiptHandle=sqs_receipt_handle
                        )
                    except Exception as e:
                        log.error(f'{str(e)}')
                        raise Exception(str(e))

                    dst_s3_object = f's3://{target_bucket}/{html_filename}'
                    success_message = f'Success: Uploaded {local_html_file} '
                    success_message += f'to {dst_s3_object}'
                    log.info(success_message)
                else:
                    error_message = f'Could not upload file to '
                    error_message += f'{dst_s3_object}: {str(e)}'
                    log.error(error_message)
                    raise Exception(error_message)
        except Exception as e:
            log.error(f'Could not convert record: {str(e)}')
            raise Exception(f'Could not convert record: {str(e)}')
        finally:
            filesToRemove = os.listdir(tmpdir)

            for f in filesToRemove:
                file_path = os.path.join(tmpdir, f)
                log.debug(f'Removing File: {file_path}')

                try:
                    os.remove(file_path)
                except OSError as e:
                    log.error(f'Could not delete file {file_path}: {str(e)}')

            log.debug(f'Removing Folder: {tmpdir}')
            os.rmdir(tmpdir)

    return('ok')
