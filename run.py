#!/usr/bin/env python3
"""Pull CloudGenix PCM data and set WAN circuit bandwidth values automatically"""

import argparse
import datetime
import os
from typing import Union, Any

import pandas as pd
from cloudgenix import API

from logger import log


def parse_args() -> argparse.Namespace:
    """Handle argument definitions and parsing of user input"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--cloudgenix-token", type=str, required=True,
        help="The API token for authenticating CloudGenix requests."
        )
    parser.add_argument(
        "-H", "--hours", type=int, default=4, metavar=4,
        help="Number of hours back from current time to calculate metrics for."
        " (default: 4)"
        )
    parser.add_argument(
        "-m", "--max", type=float, metavar=100,
        help="Maximum bandwidth capacity allowed in Mbps. This creates a "
        " ceiling."
        )
    parser.add_argument(
        "-p", "--percentile", type=int, default=95, metavar=95,
        help="Remove data point outliers by setting X percentile."
        " (default: 95)"
        )
    parser.add_argument(
        "--path-min-down", type=int, metavar=15,
        help="Minimum download capacity (Mbps) to change path policy."
        )
    parser.add_argument(
        "--path-min-up", type=int, metavar=3,
        help="Minimum upload capacity (Mbps) to change path policy."
        )
    parser.add_argument(
        "--path-policy", type=str, metavar="High Bandwidth Path Set",
        help="Name of the path stack to activate for high bandwidth sites."
        )
    parser.add_argument(
        "-t", "--tag", type=str, metavar="Offices",
        help="Filter spoke sites to those containing the specified tag."
        )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output. Intended for debugging purposes only."
        )
    args = parser.parse_args()
    if args.path_policy and not all((args.path_min_down, args.path_min_down)):
        parser.error(
            "The following arguments are required when defining a path policy"
            "config: --path-policy, --path-min-down, --path-min-up"
            )
    return args


class EnvironmentArgs:
    """Collect environment variables and make them keyname callable"""
    def __init__(self):
        self.cloudgenix_token = os.getenv("CGX_TOKEN", "")
        self.hours = int(os.getenv("HOURS", 4))
        self.max = int(os.getenv("MAX", 0))
        self.percentile = int(os.getenv("PERCENTILE", 95))
        self.path_policy = os.getenv("PATH_POLICY", "")
        self.path_min_down = int(os.getenv("PATH_MIN_DOWN", 0))
        self.path_min_up = int(os.getenv("PATH_MIN_UP", 0))
        self.tag = os.getenv("TAG", "")
        self.verbose = bool(os.getenv("VERBOSE", False))


def main() -> None:
    """Main function ran when the script is called directly"""
    # Determine whether we're running in a container or by a user
    args = EnvironmentArgs() if os.getenv("CGX_TOKEN", "") else parse_args()
    if args.verbose:
        log.setLevel("DEBUG")
        log.debug("Log level has been overriden by the --verbose argument.")
    # Initialize the CloudGenix handler
    cgx = CloudGenixHandler(token=args.cloudgenix_token)
    # Collect all sites and filter to spokes
    sites = cgx.get_sites()
    sites = [s for s in sites if s["element_cluster_role"] == "SPOKE"]
    # Filter on tag if specified
    if args.tag:
        log.info(
            "Filtering CloudGenix sites to those containing tag '%s'.", args.tag
            )
        # Sites with no tags return None so make the map conditional
        sites = [
            s for s in sites if args.tag.casefold() in
            (map(lambda x: x.casefold(), s["tags"]) if s["tags"] else [])
            ]
    log.info("Filtered to %s CloudGenix spoke sites.", len(sites))
    # If path policy name provided, get the path policy ID
    if args.path_policy:
        path_stacks = cgx.get_policy_stacks()
        path_policy_id = find_first_dict(path_stacks, "name", args.path_policy)
        path_policy_id = path_policy_id["id"]
        default_path_policy = find_first_dict(
            path_stacks, "default_policysetstack", True)
        default_path_policy = default_path_policy["id"]
        if not path_policy_id or not default_path_policy:
            raise SystemExit(
                log.error(
                    "Unable to find policy ID for path policy stack '%s'.",
                    args.path_policy
                    )
                )
    for site in sites:
        sufficient_broadband = []
        log.info(
            "Retrieving WAN interfaces for site %s (ID: %s).",
            site["name"], site["id"]
            )
        # Collect all site WAN interfaces
        wan_ints = cgx.get_wan_ints(site["id"])
        for wan_int in wan_ints:
            # Filter out WAN interfacess containing the ignore tag
            if wan_int["tags"]:
                if "auto_bw:false" in \
                        map(lambda x: x.casefold(), wan_int["tags"]):
                    log.info(
                        "%s WAN interface %s (ID: %s) contains 'auto_bw:false' "
                        "tag. Skipping.",
                        site["name"], wan_int["name"], wan_int["id"]
                        )
                    continue
            log.info("Retrieving PCM metrics for %s WAN interface %s (ID: %s).",
            site["name"], wan_int["name"], wan_int["id"]
            )
            # Query CloudGenix for WAN interface PCM metrics
            metrics_query = format_wan_metrics_query(
                site["id"], wan_int["id"], hours=args.hours)
            metrics_wan = cgx.get_wan_metrics(metrics_query)
            metrics_calced = calc_wan_int_capacity(metrics_wan, args.percentile)
            log.info(
                "Site %s WAN interface %s %s-hour download bandwidth capacity "
                "(%sth) is %sMbps.",
                site["name"], wan_int["name"], args.hours,
                args.percentile, metrics_calced["ingress_mbps"]
                )
            log.info(
                "Site %s WAN interface %s %s-hour upload bandwidth capacity "
                "(%sth) is %sMbps.",
                site["name"], wan_int["name"], args.hours,
                args.percentile, metrics_calced["egress_mbps"]
                )
            # Apply the updated bandwidth calculation
            if not (metrics_calced["ingress_mbps"]
                    or metrics_calced["egress_mbps"]):
                log.info(
                    "Site %s WAN interface %s has no usable metrics. Skipping.",
                    site["name"], wan_int["name"]
                    )
                continue
            # Set bandwidth ceiling if one provided
            if args.max:
                for k, v in metrics_calced.items():
                    metrics_calced[k] = args.max if v > args.max else v
            # Update link bandwidth values
            log.info(
                "Updating site %s WAN interface %s bandwidth capacity "
                "(%sMbps down / %sMbps up).",
                site["name"], wan_int["name"], metrics_calced["ingress_mbps"],
                metrics_calced["egress_mbps"]
                )
            wan_int["link_bw_down"] = metrics_calced["ingress_mbps"]
            wan_int["link_bw_up"] = metrics_calced["egress_mbps"]
            resp = cgx.put_wan_int(site["id"], wan_int["id"], wan_int)
            log.info(
                "CloudGenix API response status %s. Reason: %s",
                resp.status_code, resp.reason
                )
            # Determine if circuit meets requirements for high bandwidth path
            # policy stack
            if metrics_calced["ingress_mbps"] >= args.path_min_down \
                    and metrics_calced["egress_mbps"] >= args.path_min_up:
                sufficient_broadband.append(wan_int["name"])
        # Update the site network path policy stack
        if args.path_policy and sufficient_broadband:
            log.info(
                "Site %s WAN interface %s meets minimum requirements "
                "for high bandwidth path policy set.",
                site["name"], sufficient_broadband[0]
                )
            # Adjust the path policy stack if they don't match
            if site["network_policysetstack_id"] != path_policy_id:
                site["network_policysetstack_id"] = path_policy_id
                cgx.put_site(site["id"], site)
                log.info("CloudGenix API response status %s. Reason: %s",
                    resp.status_code, resp.reason
                    )
            else:
                log.info("Current path policy stack is correct. Moving on.")
        elif args.path_policy and not sufficient_broadband:
            log.info(
                "Site %s WAN interfaces do not meet minimum requirements "
                "for high bandwidth path policy set.",
                site["name"]
                )
            if site["network_policysetstack_id"] != default_path_policy:
                site["network_policysetstack_id"] = default_path_policy
                cgx.put_site(site["id"], site)
                log.info("CloudGenix API response status %s. Reason: %s",
                    resp.status_code, resp.reason
                    )
            else:
                log.info("Current path policy stack is correct. Moving on.")


def calc_wan_int_capacity(metrics: dict, percentile: int = 95) -> dict:
    """
    Calculate the average bandwidth capacity for a WAN interface

    :param metrics: CloudGenix monitor metrics containing PCM data
    :param percentile: Percentile to filter WAN collected values to
    :return: Average ingress and egress bandwidth filtered to :param percentile:
    """
    result = {"ingress_mbps": 0, "egress_mbps": 0}
    metrics_in = metrics[0].get("series")[0].get("data")[0].get("datapoints")
    metrics_out = metrics[0].get("series")[1].get("data")[0].get("datapoints")
    # Create dataframes with the info
    metrics_in = pd.DataFrame.from_dict(metrics_in)
    metrics_out = pd.DataFrame.from_dict(metrics_out)
    # Add direction column for graph plotting
    metrics_in["direction"] = "download"
    metrics_out["direction"] = "upload"
    # Remove any outliers by setting it to specified percentile
    percent = float(percentile/100)
    metrics_in_percent = metrics_in.value.quantile(percent)
    metrics_in_percent = metrics_in[metrics_in.value < metrics_in_percent]
    metrics_out_percent = metrics_out.value.quantile(percent)
    metrics_out_percent = metrics_out[metrics_out.value < metrics_out_percent]
    # Combine the dataframes
    metrics_percent = pd.concat([metrics_in_percent, metrics_out_percent])
    if not metrics_percent.empty:
        # Return calculated bandwidth values
        ingress_mbps = metrics_in_percent.mean().value
        egress_mbps = metrics_out_percent.mean().value
        result = {
            "ingress_mbps": round(ingress_mbps, 2),
            "egress_mbps": round(egress_mbps, 2)
            }
    return result


def format_wan_metrics_query(site_id: str, wan_id: str, hours: int = 4) -> dict:
    """
    Format query to collect WAN interface PCM metrics

    :param site_id: CloudGenix site ID
    :param wan_id: CloudGenix WAN link ID
    :param hours: Number of hours back from current time to include data from
    :return: CloudGenix formatted WAN metrics query
    """
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(hours=hours)
    # Format time to ISO 8601 with CloudGenix structure
    end_time = f"{end_time.replace(microsecond=0).isoformat()}.000Z"
    start_time = f"{start_time.replace(microsecond=0).isoformat()}.000Z"
    return {
        'start_time': start_time,
        'end_time': end_time,
        'interval': '5min',
        'view': {
            'summary': False,
            'individual': 'direction'
            },
        'filter': {
            'site': [site_id],
            'path': [wan_id]
            },
        'metrics': [
            {'name': 'PathCapacity', 'statistics': ['average'], 'unit': 'Mbps'}
            ]
        }


def find_first_dict(lst: list, key: str, value: Any) -> Union[dict, None]:
    """
    Attempts to find first occurence of key value pair within a list of dicts

    :param lst: List of dictionaries to search
    :param key: Key within the dictionary we wish to match
    :param value: Value within the dictionary we wish to match
    :return: Matching item in iterable if match else `None`
    """
    return next((d for d in lst if d[key] == value), None)


class CloudGenixHandler:
    """Handle interactions with the CloudGenix API and SDK"""
    def __init__(self, token: str) -> None:
        """
        :param token: CloudGenix API authentication token
        """
        self.token = token
        self.sdk = API()
        # Authenticate to the CloudGenix API
        self.login()

    def login(self) -> bool:
        """
        Authenticate to the CloudGenix portal

        :return: `True` if successful authentication else `False`
        """
        # Login to CloudGenix API
        log.info("Logging in to CloudGenix API.")
        login = self.sdk.interactive.use_token(self.token)
        if not login:
            raise ValueError(
                "Unable to login to CloudGenix API. Verify the token."
                )
        return login

    def get_policy_stacks(self) -> list:
        """
        Retrieve all network path policy stacks within the tenant

        :return: List of CloudGenix site objects
        """
        log.info("Retrieving all network path policy stacks.")
        policies = self.sdk.get.networkpolicysetstacks()
        policies = policies.cgx_content.get("items", [])
        log.info("Retrieved %s CloudGenix path policy stacks.", len(policies))
        return policies

    def get_sites(self) -> list:
        """
        Retrieve all sites within the tenant from the CloudGenix API

        :return: List of CloudGenix site objects
        """
        log.info("Retrieving all CloudGenix sites.")
        sites = self.sdk.get.sites()
        sites = sites.cgx_content.get("items", [])
        log.info("Retrieved %s CloudGenix sites.", len(sites))
        return sites

    def get_wan_ints(self, site_id: str) -> list:
        """
        Retrieve all WAN interfaces configured for :param site:

        :return: List of CloudGenix WAN interface objects
        """
        wan_ints = self.sdk.get.waninterfaces(site_id=site_id)
        wan_ints = wan_ints.cgx_content.get("items", [])
        log.info("Retrieved %s CloudGenix WAN interfaces.", len(wan_ints))
        return wan_ints

    def get_wan_metrics(self, query: dict) -> list:
        """
        Retrieve WAN metrics from CloudGenix monitoring endpoint

        :param query: CloudGenix formatted WAN metrics query
        :return: List of CloudGenix WAN interface metric objects
        """
        metrics = self.sdk.post.monitor_metrics(query)
        metrics = metrics.cgx_content.get("metrics", [])
        return metrics

    def put_site(self, site_id: str, data: dict) -> dict:
        """
        Update a site with the information provided in :param data:

        :param site_id: CloudGenix site ID
        :param data: CloudGenix site data to PUT as JSON
        :return: CloudGenix API response to PUT request
        """
        log.info("Requesting CloudGenix API PUT for site %s.", site_id)
        resp = self.sdk.put.sites(site_id, data)
        return resp

    def put_wan_int(self, site_id: str, wan_interface_id: str,
            data: dict) -> dict:
        """
        Update a WAN interface with information provided in :param data:

        :param site_id: CloudGenix site ID
        :param wan_interface_id: WAN interface ID
        :param data: CloudGenix WAN interface data to PUT as JSON
        :return: CloudGenix API response to PUT request
        """
        resp = self.sdk.put.waninterfaces(site_id, wan_interface_id, data)
        return resp


if __name__ == "__main__":
    main()
