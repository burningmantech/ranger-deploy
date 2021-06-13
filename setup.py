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

from pathlib import Path
from sys import path
from typing import Dict, List

from setuptools import find_packages, setup

path.insert(0, "src")

from deploy import __version__ as version_string  # noqa: E402


#
# Options
#

name = "ranger-deploy"

description = "Deployment tools for Ranger services"

readme_path = Path(__file__).parent / "README.rst"
long_description = readme_path.open().read()

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
    "Programming Language :: Python :: 3.7",
    "Topic :: Software Development :: Build Tools",
    "Topic :: Software Development :: Libraries :: Python Modules",
]


#
# Entry points
#

entry_points: Dict[str, List[str]] = {
    "console_scripts": [],
}

script_entry_points = {
    "aws_ecr": ("deploy.aws.ecr", "ECRServiceClient.main"),
    "aws_ecs": ("deploy.aws.ecs", "ECSServiceClient.main"),
    "notify_smtp": ("deploy.notify.smtp", "SMTPNotifier.main"),
}

for tool, (module, function) in script_entry_points.items():
    entry_points["console_scripts"].append(
        f"deploy_{tool} = {module}:{function}"
    )


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

python_requirements = ">=3.6"

setup_requirements: List[str] = []

install_requirements = [
    # We are not pinning patch version for Boto because:
    # • it changes very frequently
    # • it is reliably compatible
    # • it should improve interoperability with AWS services
    # Direct dependencies
    "arrow==1.1.0",
    "attrs==21.2.0",
    "boto3>=1.17,<1.18",
    "Click==8.0.1",
    "docker==5.0.0",  # [tls]
    "GitPython==3.1.17",
    #"Twisted",  # See dependency_links below
    # Indirect dependencies
    "Automat==20.2.0",
    "botocore>=1.20,<1.21",
    "certifi==2021.5.30",
    "chardet==4.0.0",
    "constantly==15.1.0",
    "docutils==0.17.1",
    "gitdb2==4.0.2",
    "hyperlink==21.0.0",
    "idna==2.10",  # rq.filter: <3.0
    "incremental==21.3.0",
    "jmespath==0.10.0",
    "PyHamcrest==2.0.2",
    "python-dateutil==2.8.1",
    "requests==2.25.1",
    "s3transfer==0.4.2",
    "six==1.16.0",
    "smmap2==3.0.1",
    "urllib3==1.26.5",
    "websocket-client==1.0.1",
    "zope.interface==5.4.0",
]

extras_requirements: Dict[str, List[str]] = {}


#
# Set up Extension modules that need to be built
#

extensions: List[str] = []


#
# Run setup
#


def main() -> None:
    """
    Run :func:`setup`.
    """
    setup(
        name=name,
        version=version_string,
        description=description,
        long_description=long_description,
        long_description_content_type="text/x-rst",
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
        python_requires=python_requirements,
        setup_requires=setup_requirements,
        install_requires=install_requirements,
        extras_require=extras_requirements,
        # Use a pinned version for newer type hints
        dependency_links=[
            "https://github.com/twisted/Twisted/tarball/f1d82accda4953aae8049545bce8763f8aab75fb.zip#egg=Twisted"
        ],
    )


#
# Main
#

if __name__ == "__main__":
    main()
