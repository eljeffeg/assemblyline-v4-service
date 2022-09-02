import os

from assemblyline.common.importing import load_module_by_path
from assemblyline_v4_service.testing.helper import TestHelper

required_env = [
    'SERVICE_MANIFEST_PATH',
    'SERVICE_TESTING_RESULT_FOLDER',
    'SERVICE_PATH'
]

optional_env = [
    'SERVICE_TESTING_EXTRA_SAMPLE_FOLDER',
    'FULL_SAMPLES_LOCATION'
]


def run():
    for env in required_env:
        if os.environ.get(env, None) is None:
            print(f"[E] You must set {env} environement variable.")
            exit(1)

    for env in optional_env:
        if os.environ.get(env, None) is None:
            print(f"[W] {env} environement variable is not set, it should probably be...")

    extra_folder = os.environ.get('SERVICE_TESTING_EXTRA_SAMPLE_FOLDER', None)
    if extra_folder:
        extra_folder = [extra_folder]

    th = TestHelper(load_module_by_path(os.environ['SERVICE_PATH']),
                    os.environ['SERVICE_TESTING_RESULT_FOLDER'], extra_folder)
    th.regenerate_results()


if __name__ == "__main__":
    run()
