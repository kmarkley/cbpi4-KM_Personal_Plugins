from setuptools import setup

# read the contents of your README file
from os import path
this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(name='cbpi4-KM_Personal_Plugins',
      version='0.0.3',
      description='CraftBeerPi4 Personal Plugins',
      author='Kirk Markley',
      url='https://github.com/kmarkley/cbpi4-KM_Personal_Plugins',
      license='GPLv3',
      include_package_data=True,
      package_data={
        # If any package contains *.txt or *.rst files, include them:
      '': ['*.txt', '*.rst', '*.yaml'],
      'cbpi4-KM_Personal_Plugins': ['*','*.txt', '*.rst', '*.yaml']},
      packages=['cbpi4-KM_Personal_Plugins'],
      install_requires=[
            'cbpi',
      ],
      long_description=long_description,
      long_description_content_type='text/markdown'
     )
