from distutils.core import setup

setup(
    name='csvquerytool',
    version='0.0.1',
    author='Alister Cordiner',
    author_email='alister@cordiner.net',
    packages=['csvquerytool', 'csvquerytool.test'],
    scripts=['bin/csvquery'],
    url='http://pypi.python.org/pypi/csvquerytool/',
    license='LICENSE.txt',
    description='Execute SQL queries on CSV files.',
    long_description=open('README.txt').read(),
    install_requires=[],
)