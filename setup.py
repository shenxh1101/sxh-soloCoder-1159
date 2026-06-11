from setuptools import setup, find_packages

setup(
    name="docker-slim",
    version="1.0.0",
    description="Docker Image Layer Analysis & Slimming Tool",
    author="docker-slim",
    packages=find_packages(),
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "docker-slim = docker_slim.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
    ],
)