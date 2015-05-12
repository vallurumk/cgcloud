import os
from subprocess import check_output
from pkg_resources import parse_version
from setuptools import setup, find_packages

cgcloud_version = '1.0.dev1'

dependency_links = [ ]


def add_private_dependency( name, version=cgcloud_version, git_ref=None ):
    if git_ref is None:
        if parse_version( version ).is_prerelease:
            project_dir = os.path.dirname( os.path.abspath( __file__ ) )
            git_ref = check_output( [ 'git', 'rev-parse', '--abbrev-ref', 'HEAD' ],
                                    cwd=project_dir ).strip( )
            # pip checks out individual commits which creates a detached HEAD, so we look at
            # remote branches containing the commit
            if git_ref == 'HEAD':
                git_ref = check_output( [ 'git', 'branch', '-r', '--contains', 'HEAD' ],
                                        cwd=project_dir ).strip( )
                # Just take the first branch
                # FIXME: this is, of course, silly
                git_ref = git_ref.split('\n')[0]
                # Split remote from branch name
                git_ref = git_ref.split( '/' )
                assert len( git_ref ) == 2
                git_ref = git_ref[ 1 ]
        else:
            git_ref = version
    url = 'git+https://github.com/BD2KGenomics'
    dependency_links.append(
        '{url}/{name}.git@{git_ref}#egg={name}-{version}'.format( **locals( ) ) )
    return '{name}=={version}'.format( **locals( ) )


setup(
    name='cgcloud-lib',
    version=cgcloud_version,

    author='Hannes Schmidt',
    author_email='hannes@ucsc.edu',
    url='https://github.com/BD2KGenomics/cgcloud-lib',
    description='Components shared between cgcloud-core and cgcloud-agent',

    package_dir={ '': 'src' },
    packages=find_packages( 'src' ),
    install_requires=[
        add_private_dependency( 'bd2k-python-lib', '1.5' ),
        'boto>=2.36.0'
    ],
    setup_requires=[
        'nose>=1.3.4' ],
    dependency_links=dependency_links,
    namespace_packages=[ 'cgcloud' ] )
