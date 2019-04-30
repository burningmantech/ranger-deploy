#!/usr/bin/env python3

##
# See the file COPYRIGHT for copyright information.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

"""
Setuptools configuration
"""

import sys

if sys.version_info < (3, 6, 0):
    sys.stderr.write("ERROR: Python 3.6 or later is required.\n")
    exit(1)

from pathlib import Path  # noqa
from setuptools import setup, find_packages  # noqa

sys.path.insert(0, "src")

from deploy import __version__ as version_string  # noqa


#
# Options
#

name = "ranger-deploy"

description = "Deployment tools for Ranger services"

readme_path = Path(__file__).parent / "README.rst"
try:
    long_description = readme_path.open().read()
except IOError:
    long_description = None

url = "https://github.com/burningmantech/ranger-deploy"

author = "Burning Man"

author_email = "ranger-tech-ninjas@burningman.org"

license = "Apache License, Version 2.0"

platforms = ["all"]

packages = find_packages(where="src")

classifiers = [
    "Intended Audience :: Information Technology",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3.6",
]


#
# Entry points
#

entry_points = {
    "console_scripts": [],
}

script_entry_points = {
    "aws_ecr":     ("deploy.aws.ecr",     "ECRServiceClient.main"),
    "aws_ecs":     ("deploy.aws.ecs",     "ECSServiceClient.main"),
    "notify_smtp": ("deploy.notify.smtp", "SMTPNotifier.main"    ),
}

for tool, (module, function) in script_entry_points.items():
    entry_points["console_scripts"].append(f"deploy_{tool} = {module}:{function}")


#
# Package data
#

package_data = dict(
    deploy=[
        "notify/templates/message.*",
    ],
)


#
# Dependencies
#

setup_requirements = []

install_requirements = [
    # Direct dependencies
    "arrow==0.13.1",
    "attrs==19.1.0",
    "boto3==1.9.138",
    "click==7.0",
    "docker==3.7.2",  # [tls]
    "Twisted==19.2.0",

    # Indirect dependencies
    "Automat==0.7.0",
    "botocore==1.12.138",
    "constantly==15.1.0",
    "docutils==0.14",
    "hyperlink==19.0.0",
    "idna==2.8",
    "incremental==17.5.0",
    "jmespath==0.9.4",
    "PyHamcrest==1.9.0",
    "python-dateutil==2.8.0",
    "s3transfer==0.2.0",
    "six==1.12.0",
    "urllib3==1.25.2",
    "zope.interface==4.6.0",
]

extras_requirements = {}


#
# Set up Extension modules that need to be built
#

extensions = []


#
# Run setup
#

def main():
    """
    Run :func:`setup`.
    """
    setup(
        name=name,
        version=version_string,
        description=description,
        long_description=long_description,
        url=url,
        classifiers=classifiers,
        author=author,
        author_email=author_email,
        license=license,
        platforms=platforms,
        packages=packages,
        package_dir={"": "src"},
        package_data=package_data,
        entry_points=entry_points,
        data_files=[],
        ext_modules=extensions,
        setup_requires=setup_requirements,
        install_requires=install_requirements,
        extras_require=extras_requirements,
    )


#
# Main
#

if __name__ == "__main__":
    main()
