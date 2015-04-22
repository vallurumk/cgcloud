from pkg_resources import parse_version
from setuptools import setup, find_packages

dependency_links = [ ]

cgcloud_version = '1.0.dev1'


def add_private_dependency( name, version=cgcloud_version, git_ref=None ):
    if git_ref is None:
        git_ref = 'master' if parse_version( version ).is_prerelease else version
    url = 'git+https://github.com/BD2KGenomics'
    dependency_links.append(
        '{url}/{name}.git@{git_ref}#egg={name}-{version}'.format( **locals( ) ) )
    return '{name}=={version}'.format( **locals( ) )


setup(
    name='cgcloud-spark',
    version=cgcloud_version,

    author='Hannes Schmidt',
    author_email='hannes@ucsc.edu',
    url='https://github.com/BD2KGenomics/cgcloud-spark',
    description='Setup and manage a Apache Spark cluster in EC2',

    package_dir={ '': 'src' },
    packages=find_packages( 'src' ),
    package_data={
        'cgcloud.spark': [
            'cgcloud-sparkbox-tools/setup.py',
            'cgcloud-sparkbox-tools/cgcloud/__init__.py',
            'cgcloud-sparkbox-tools/cgcloud/spark_tools/__init__.py' ] },
    install_requires=[
        add_private_dependency( 'cgcloud-lib' ),
        add_private_dependency( 'cgcloud-core' ),
        'Fabric>=1.7.0',
        'lxml>=3.2.1'
    ],
    setup_requires=[
        'nose>=1.3.4' ],
    namespace_packages=[ 'cgcloud' ],
    dependency_links=dependency_links )
