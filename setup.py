from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="odta",
    version="0.1.0",
    author="ODTA Team",
    author_email="support@odta.com",
    description="One-Day Trading Agent - Free-tier trading assistant for intraday opportunities",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/fin_agent_v0_050625",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Financial and Insurance Industry",
        "Topic :: Office/Business :: Financial :: Investment",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.11",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "odta=src.main:cli",
            "odta-scan=src.main:run_scan_cli",
            "odta-dashboard=dashboard:main",
        ],
    },
    include_package_data=True,
    package_data={
        "": ["*.csv", "*.json", "*.yaml"],
    },
)