from setuptools import find_packages, setup

setup(
    name="thirsty",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "gpxpy==1.6.2",
        "requests==2.32.3",
        "rich==14.0.0",
        "folium==0.19.5",
        "scipy==1.13.0",
    ],
    extras_require={
        "dev": [
            "pre-commit==4.2.0",
            "commitizen==4.6.0",
        ]
    },
    entry_points={
        "console_scripts": [
            "thirsty=thirsty.cli:main"
        ]
    },
    author="Jean Leroy",
    description="Add to a GPX trace nearby water POI",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
    ],
    python_requires=">=3.9",
)
