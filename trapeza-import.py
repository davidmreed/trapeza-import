#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  trapeza-import.py
#  
#  Copyright 2013-2014 David Reed <david@ktema.org>
#  This file is available under the terms of the MIT License.
#

import trapeza
import trapeza.match
import trapeza.formats
import tempfile
import pickle
import io
import os
import hashlib
from flask import Flask, request, session, make_response, render_template, flash, redirect, url_for
from operator import itemgetter, attrgetter
from itertools import groupby

# Accept three Source objects. Run match_incoming_records() on them.
# Render a template showing the user matches and allowing them to select a 
# matching record.
# On selection, run diff on the selected records (comparing the columns that are 
# mapped by the profile) and offer the user the chance to select one of the two versions 
# of each field or to enter their own data.
# On output, generate a copy of the input sheet with a unique ID column added specifying
# the matched record in the database. For each diffed column, if the user did not choose 
# to retain existing data, include the user-input value or the value from incoming.
# Maintain all other columns in the original input sheet unaltered for import.

app = Flask(__name__)
app.secret_key = "THIS_IS_A_TESTING_SECRET_KEY"

encodings = {
    "utf8": "UTF-8",
    "utf-16": "UTF-16",
    "latin_1": "Latin-1",
    "mac_roman": "Mac OS Roman",
    "cp1252": "Windows Codepage 1252"
}


# Filters and functions for use within the match template
def flatten_record(record, headers):
    return u"; ".join([u"{0}: {1}".format(key, record.values[key]) for key in headers])


def get_identifier(element="", incoming_line=None, record_id="", key=""):
    if not isinstance(incoming_line, str):
        line = str(incoming_line)
    else:
        line = incoming_line or ""

    return u"{0}{1}".format(element, hashlib.sha512(".".join([line, record_id or "", key or ""])).hexdigest())


def generate_header_mapping(profile):
    header_map = {}

    for mapping in profile.mappings:
        if mapping.key not in header_map:
            header_map[mapping.key] = mapping.master_key

    return header_map


def group_results(results, nresults):
    list_results = []

    gb = groupby(results, lambda res: res.incoming.input_line())

    for (k, g) in gb:
        list_results.append((k, sorted(list(g), key=attrgetter("score"), reverse=True)[:nresults]))

    return sorted(list_results, key=itemgetter(0))


@app.route("/")
def start():
    return render_template("index.html",
                           encodings=sorted(encodings.items(), key=itemgetter(1)),
                           input_formats=trapeza.formats.available_input_formats(),
                           output_formats=trapeza.formats.available_output_formats())


@app.route("/run", methods=["POST"])
def step_one():
    try:
        # Load master sheet or processed master file and profile
        master_file = request.files["master"]

        if "input_format" in request.form \
                and request.form.get("input_format") in trapeza.formats.available_input_formats():
            master_format = request.form.get("input_format")
        else:
            master_format = trapeza.get_format(master_file.filename, "csv")

        if "input_encoding" in request.form and request.form.get("input_encoding") in encodings:
            input_encoding = request.form.get("input_encoding")
        else:
            input_encoding = "utf8"

        if master_format == "trapeza":
            # Master file has been preprocessed
            processed_master = pickle.load(master_file)
            master = processed_master.source
            profile = processed_master.profile
        else:
            processed_master = None
            master = trapeza.load_source(request.files["master"], master_format, encoding=input_encoding)
            profile = trapeza.match.Profile(
                source=trapeza.load_source(request.files["profile"],
                                           trapeza.get_format(request.files["profile"].filename, "csv"),
                                           encoding=input_encoding))

        # Load incoming file
        if "input_format" in request.form \
                and request.form.get("input_format") in trapeza.formats.available_input_formats():
            incoming_format = request.form.get("input_format")
        else:
            incoming_format = trapeza.get_format(request.files["incoming"].filename, "csv")

        incoming = trapeza.load_source(request.files["incoming"], incoming_format, encoding=input_encoding)
    except IOError:
        flash("An error occurred during loading of input files. Please provide files in a format that Trapeza "
              "understands and ensure you choose the correct text encoding.")
        return redirect(url_for("start"))

    # Determine primary key and set in Master. We do not set the primary key on incoming or the output.
    # If the user feels like matching multiple input lines to the same master records, that's fine;
    # we don't want to throw an exception.
    try:
        primary_key = request.form["primary_key"]
        master.set_primary_key(primary_key)
    except KeyError:
        flash("One or more master records are missing the specified primary key.")
        return redirect(url_for("start"))

    # Bring in the parameters.
    try:
        cutoff = abs(int(request.form["cutoff"])) or 0
        nresults = abs(int(request.form["nresults"])) or 5
        display_diff = "display_diff" in request.form
        include_unmatched_records = "include_unmatched_records" in request.form
        output_only_modified_entries = "output_only_modified_entries" in request.form
        include_re_new_address_flag = "include_re_new_address_flag" in request.form
        if request.form.get("line_endings") in ["cr", "crlf", "lf"]:
            line_endings = request.form.get("line_endings")
        else:
            line_endings = "lf"
    except [KeyError, TypeError]:
        flash("An invalid option was specified.")
        return redirect(url_for("start"))

    # Perform the comparison.
    try:
        results = group_results(profile.compare_sources(processed_master or master, incoming, cutoff), nresults)
    except Exception as e:
        flash("An error occurred during matching ({})".format(e))
        return redirect(url_for("start"))

    try:
        # Save the details of the operation
        if request.form.get("output_format") and \
           request.form.get("output_format") in trapeza.formats.available_output_formats():
            output_format = request.form.get("output_format")
        else:
            output_format = "csv"

        if request.form.get("output_encoding") and request.form.get("output_encoding") in encodings:
            output_encoding = request.form.get("output_encoding")
        else:
            output_encoding = "utf8"

        operation = {"master": master, "incoming": incoming, "profile": profile,
                     "primary_key": primary_key, "results": results,
                     "cutoff": cutoff,
                     "output_format": output_format,
                     "output_encoding": output_encoding,
                     "line_endings": line_endings,
                     "display_diff": display_diff,
                     "include_unmatched_records": include_unmatched_records,
                     "output_only_modified_entries": output_only_modified_entries,
                     "include_re_new_address_flag": include_re_new_address_flag}

        outfile = tempfile.NamedTemporaryFile(delete=False)
        session["file"] = outfile.name
        pickle.dump(operation, outfile, pickle.HIGHEST_PROTOCOL)
        outfile.close()
    except Exception as e:
        flash("An error occurred while saving output ({})".format(e))
        return redirect(url_for("start"))

    return render_template("match.html",
                           results=results,
                           primary_key=primary_key,
                           header_map=generate_header_mapping(profile),
                           incoming_headers=incoming.headers(),
                           master_headers=master.headers(),
                           flatten_record=flatten_record,
                           get_identifier=get_identifier,
                           display_diff=display_diff,
                           include_re_new_address_flag=include_re_new_address_flag)


@app.route("/dl", methods=["POST"])
def step_two():
    try:
        with open(session["file"], "rb") as sess_file:
            operation = pickle.load(sess_file)

        os.unlink(session["file"])
    except IOError:
        flash("An error occurred while loading processed results.")
        return redirect(url_for("start"))

    try:
        primary_key = operation["primary_key"]
        header_map = generate_header_mapping(operation["profile"])

        output = trapeza.Source(operation["incoming"].headers())
        output.add_column(primary_key)

        if operation["include_re_new_address_flag"]:
            if "New Address?" not in output.headers():
                output.add_column("New Address?")

        matched_records = []

        for (original_line, matches) in operation["results"]:

            matched_records.append(original_line)

            master_id = request.form.get(get_identifier('select', original_line))

            if master_id or operation["include_unmatched_records"]:
                out_record = trapeza.Record(matches[0].incoming.values)
                out_record.values[primary_key] = master_id or u""
                modified = False

                for key in out_record.values:
                    if key != primary_key:
                        value = request.form.get(get_identifier("select", original_line, master_id, key))

                        if value == "MASTER":
                            if not operation["output_only_modified_entries"]:
                                out_record.values[key] = operation["master"].get_record_with_id(master_id).values[
                                    header_map[key]]
                            else:
                                out_record.values[key] = ""
                        elif value == "USER":
                            user_val = request.form.get(get_identifier("userentrybox", original_line, master_id, key))
                            if value is not None and len(value) > 0:
                                out_record.values[key] = user_val
                                modified = True
                        elif value == "INCOMING" or value == "" or value is None:
                            # If the user made no selection, assume that we're to retain the incoming data.
                            modified = True
                            pass
                        else:
                            raise Exception("Invalid form data.")

                if operation["include_re_new_address_flag"]:
                    new_address = get_identifier('newaddressbox', original_line) in request.form
                    out_record.values["New Address?"] = "TRUE" if modified and new_address else "FALSE"

                output.add_record(out_record)

        # If we are outputting unmatched records, we also must collect and output any record which didn't
        # have a match over the cutoff (which won't appear in the matched list)

        for record in operation["incoming"].records():
            if record.input_line() not in matched_records:
                out_record = trapeza.Record(record.values)
                out_record.values[primary_key] = u""
                if operation["include_re_new_address_flag"]:
                    out_record.values["New Address?"] = "FALSE"

                output.add_record(record)
    except Exception as e:
        flash("An error occurred while processing matches ({})".format(e))
        return redirect(url_for("start"))

    try:
        out_buffer = io.BytesIO()

        endings = {"crlf": "\r\n", "lf": "\n", "cr": "\r"}

        trapeza.write_source(output, out_buffer, operation["output_format"], encoding=operation["output_encoding"],
                             line_endings=endings.get(operation["line_endings"]))

        data = out_buffer.getvalue()
        out_buffer.close()

        response = make_response(data)
        response.headers["Content-Disposition"] = "attachment; filename=output.{}".format(operation["output_format"])
        response.headers["Content-Type"] = "application/octet-stream"

        return response
    except Exception as e:
        flash("An error occurred while writing output ({})".format(e))
        return redirect(url_for("start"))


if __name__ == "__main__":
    app.debug = True
    app.run()
