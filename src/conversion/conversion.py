import json
import os
import sys

import boto3
import botocore
import markdown
import tempfile
import logging

max_object_size = 104857600  # 100MB = 104857600 bytes

target_bucket = os.getenv('TARGET_BUCKET')

logging_level = 'logging.' + os.getenv('LOGGING_LEVEL')
print(logging_level)

s3_resource = boto3.resource('s3')

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(level=logging.INFO, format='%(message)s')


class StructuredMessage(object):
    def __init__(self, message, **kwargs):
        self.message = message
        self.kwargs = kwargs

    def __str__(self):
        return '%s >>> %s' % (self.message, json.dumps(self.kwargs))


_ = StructuredMessage   # optional, to improve readability
logging.info(_('message 1', foo='bar', bar='baz', num=123, fnum=123.456))


def check_s3_object_size(bucket, key_name):
    try:
        size = s3_resource.Object(bucket, key_name).content_length
    except Exception as e:
        print('Error: {}'.format(str(e)))
        size = 'NaN'

    return size


def get_s3_object(bucket, key_name, local_file):
    try:
        s3_resource.Bucket(bucket).download_file(key_name, local_file)
        return 'ok'
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return 'Error: s3://{}/{} does not exist'.format(bucket, key_name)
        else:
            return 'Error: {}'.format(str(e))


def convert_to_html(file):
    try:
        file_open = open(file, 'r')
        file_string = file_open.read()
        file_open.close()

    except Exception as e:
        print('Error: {}'.format(str(e)))
        raise

    return markdown.markdown(file_string)


def upload_html(target_bucket, target_key, source_file):
    try:
        s3_resource.Object(target_bucket, target_key).upload_file(source_file)
        html_upload = 'ok'
    except Exception as e:
        print('Error: {}'.format(str(e)))
        html_upload = 'fail'

    return html_upload


def handler(event, context):
    for record in event['Records']:
        tmpdir = tempfile.mkdtemp()

        logging.info(_('EventInfo',
                       request_id=context.aws_request_id,
                       invoked_function_arn=context.invoked_function_arn,
                       sqs_message_id=record['messageId'],
                       sqs_event_source_arn=['eventSourceARN']))
        try:
            json_body = json.loads(record['body'])
            request_params = json_body['detail']['requestParameters']
            bucket_name = request_params['bucketName']
            key_name = request_params['key']

            size = check_s3_object_size(bucket_name, key_name)

            local_file = os.path.join(tmpdir, key_name)

            download_status = get_s3_object(bucket_name, key_name, local_file)

            if download_status == 'ok':
                key_bytes = os.stat(local_file).st_size
                logging.info(_('S3DownloadSucess',
                               src_s3_download='ok',
                               src_s3_download_bytes=key_bytes,
                               source_s3_bucket_name=bucket_name,
                               source_s3_key_name=key_name))
            else:
                logging.info(_('S3DownloadFailure',
                               src_s3_download=download_status,
                               src_s3_download_bytes=-1,
                               source_s3_bucket_name=bucket_name,
                               source_s3_key_name=key_name))
                sys.exit(1)

            html = convert_to_html(local_file)

            html_filename = os.path.splitext(key_name)[0] + '.html'

            local_html_file = os.path.join(tmpdir, html_filename)

            with open(local_html_file, 'w') as outfile:
                outfile.write(html)

            outfile.close()

            html_upload = upload_html(target_bucket,
                                      html_filename,
                                      local_html_file)

            if html_upload == 'ok':
                logging.info(_('DestinationObject',
                               dst_s3_object=f's3://{target_bucket}/{html_filename}'))
            else:
                logging.info(_('DestinationObject', dst_s3_object=''))

            logging.info(_('DestinationUpload', dst_s3_upload=html_upload))

        except Exception as e:
            logging.info(_('ProcessingFailure', error_msg=str(e)))
            return 'fail'

        finally:
            filesToRemove = os.listdir(tmpdir)
  
            for f in filesToRemove:
                file_path = os.path.join(tmpdir, f)
                logging.info(_('Cleanup', removed_file=file_path))

                try:
                    os.remove(file_path)
                except OSError as e:
                    print(e)
                    logging.info(_('CleanupFailed', could_not_remove_file=file_path))

            logging.info(_('Cleanup', removed_folder=tmpdir))
            os.rmdir(tmpdir)

        #print(log_event)
        return 'ok'
