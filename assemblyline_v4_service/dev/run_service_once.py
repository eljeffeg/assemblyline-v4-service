import argparse
import json
import logging
import os
import pprint
import shutil
import tempfile
import yaml

from cart import unpack_stream
from typing import Union, Dict

from assemblyline.common import identify
from assemblyline.common.heuristics import service_heuristic_to_result_heuristic, InvalidHeuristicException
from assemblyline.common.importing import load_module_by_path
from assemblyline.common.isotime import now_as_iso
from assemblyline.common.uid import get_random_id
from assemblyline.odm.messages.task import Task as ServiceTask
from assemblyline.odm.models.result import Result
from assemblyline.odm.models.service import Service
from assemblyline_v4_service.common.helper import get_heuristics


class RunService:
    def __init__(self):
        self.service = None
        self.service_class = None
        self.submission_params = None
        self.file_dir = None

    def try_run(self):
        try:
            self.service_class = load_module_by_path(SERVICE_PATH)
        except ValueError:
            raise
        except Exception:
            LOG.error("Could not find service in path. Check your environment variables.")
            raise

        self.load_service_manifest()

        if not os.path.isfile(FILE_PATH):
            LOG.info(f"File not found: {FILE_PATH}")
            return

        self.file_dir = os.path.dirname(FILE_PATH)

        # Get filename and working dir
        file_name = os.path.basename(FILE_PATH)
        working_dir = os.path.join(self.file_dir, f'{os.path.basename(FILE_PATH)}_{SERVICE_NAME.lower()}')

        # Start service
        self.service.start_service()

        # Identify the file
        file_info = identify.fileinfo(FILE_PATH)
        if file_info['type'] == "archive/cart":
            # This is a CART file, uncart it and recreate the file info object
            original_temp = os.path.join(tempfile.gettempdir(), file_info['sha256'])
            with open(FILE_PATH, 'rb') as ifile, open(original_temp, 'wb') as ofile:
                unpack_stream(ifile, ofile)

            file_info = identify.fileinfo(original_temp)
            target_file = os.path.join(tempfile.gettempdir(), file_info['sha256'])
            shutil.move(original_temp, target_file)
            LOG.info(f"File was a CaRT archive, it was un-CaRTed to {target_file} for processing")

        else:
            # It not a cart, move the file to the right place to be processed
            target_file = os.path.join(tempfile.gettempdir(), file_info['sha256'])
            shutil.copyfile(FILE_PATH, target_file)

        # Create service processing task
        service_task = ServiceTask(dict(
            sid=get_random_id(),
            service_name=SERVICE_NAME,
            service_config=self.submission_params,
            fileinfo=dict(
                magic=file_info['magic'],
                md5=file_info['md5'],
                mime=file_info['mime'],
                sha1=file_info['sha1'],
                sha256=file_info['sha256'],
                size=file_info['size'],
                type=file_info['type'],
            ),
            filename=file_name,
            max_files=501,  # TODO: get the actual value
            ttl=3600,
        ))

        LOG.info(f"Starting task with SID: {service_task.sid}")

        # Set the working directory to a directory with same parent as input file
        if os.path.isdir(working_dir):
            shutil.rmtree(working_dir)
        if not os.path.isdir(working_dir):
            os.makedirs(os.path.join(working_dir, 'working_directory'))

        self.service.handle_task(service_task)

        # Move the result.json and extracted/supplementary files to the working directory
        source = os.path.join(tempfile.gettempdir(), 'working_directory')
        if not os.path.exists(source):
            os.makedirs(source)

        files = os.listdir(source)
        for f in files:
            shutil.move(os.path.join(source, f), os.path.join(working_dir, 'working_directory'))

        # Cleanup files from the original directory created by the service base
        shutil.rmtree(source)

        result_json = os.path.join(tempfile.gettempdir(),
                                   f'{service_task.sid}_{service_task.fileinfo.sha256}_result.json')

        if not os.path.exists(result_json):
            raise Exception("A service error occured and no result json was found.")

        # Validate the generated result
        with open(result_json, 'r') as fh:
            try:
                result = json.load(fh)
                result.pop('temp_submission_data', None)
                for file in result['response']['extracted'] + result['response']['supplementary']:
                    file.pop('path', None)

                # Load heuristics
                heuristics = get_heuristics()

                # Transform heuristics and calculate score
                total_score = 0
                for section in result['result']['sections']:
                    if section['heuristic']:
                        heur_id = section['heuristic']['heur_id']

                        try:
                            section['heuristic'] = service_heuristic_to_result_heuristic(section['heuristic'],
                                                                                         heuristics)
                            total_score += section['heuristic']['score']
                        except InvalidHeuristicException:
                            section['heuristic'] = None
                        section['heuristic']['name'] = heuristics[heur_id]['name']
                result['result']['score'] = total_score

                # Add timestamps for creation, archive and expiry
                result['created'] = now_as_iso()
                result['archive_ts'] = now_as_iso(1 * 24 * 60 * 60)
                result['expiry_ts'] = now_as_iso(service_task.ttl * 24 * 60 * 60)

                result = Result(result)

                # Print the result on console if in debug mode
                if args.debug:
                    f"{SERVICE_NAME.upper()}-RESULT".center(60, '-')
                    for line in pprint.pformat(result.result.as_primitives()).split('\n'):
                        LOG.debug(line)
            except Exception as e:
                LOG.error(f"Invalid result created: {str(e)}")

        LOG.info(f"Cleaning up file used for temporary processing: {target_file}")
        os.unlink(target_file)

        LOG.info(f"Moving {result_json} to the working directory: {working_dir}/result.json")
        shutil.move(result_json, os.path.join(working_dir, 'result.json'))

        LOG.info(f"Successfully completed task. Output directory: {working_dir}")

    def stop(self):
        self.service.stop_service()

    def load_service_manifest(self, return_heuristics=False) -> Union[None, Dict]:
        service_manifest_yml = os.path.join(os.getcwd(), 'service_manifest.yml')

        if os.path.exists(service_manifest_yml):
            with open(service_manifest_yml) as yml_fh:
                service_manifest_data = yaml.safe_load(yml_fh.read())

            heuristics = service_manifest_data.get('heuristics', None)

            # Pop the 'extra' data from the service manifest
            for x in ['file_required', 'tool_version', 'heuristics']:
                service_manifest_data.pop(x, None)

            # Validate the service manifest
            try:
                self.service = Service(service_manifest_data)
            except Exception as e:
                LOG.error(f"Invalid service manifest: {str(e)}")

            service_config = {}
            if service_manifest_data:
                service_config = service_manifest_data.get('config', {})

            self.submission_params = {x['name']: x['default'] for x in service_manifest_data.get('submission_params', [])}

            self.service = self.service_class(config=service_config)
            if return_heuristics:
                return heuristics
        else:
            raise Exception("Service manifest YAML file not found in root folder of service.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--debug", action="store_true", help="turn on debugging mode")
    parser.add_argument("service_path", help="python path of the service")
    parser.add_argument("file_path", help="file path of the file to be processed")

    args = parser.parse_args()

    SERVICE_PATH = args.service_path
    SERVICE_NAME = SERVICE_PATH.split(".")[-1].lower()
    FILE_PATH = args.file_path

    # create logger
    LOG = logging.getLogger(f"assemblyline.service.{SERVICE_NAME}")
    if args.debug:
        LOG.setLevel(logging.DEBUG)
    else:
        LOG.setLevel(logging.INFO)

    rs = RunService()
    rs.try_run()
