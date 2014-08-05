#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  trapeza-import.py
#  
#  Copyright 2013-2014 David Reed <david@ktema.org>
#  This file is available under the terms of the MIT License.
#

import trapeza, trapeza.match, tempfile, pickle, cStringIO, os, hashlib
from flask import Flask, request, session, make_response, render_template, abort
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

# Filters and functions for use within the match template
def flatten_record(record, headers):
    return u"; ".join([u"{0}: {1}".format(key, record.values[key]) for key in headers])

def get_identifier(element = "", incoming_line = None, record_id = "", key = ""):
    if not isinstance(incoming_line, str):
        line = str(incoming_line)
    else:
        line = incoming_line or ""
        
    return u"{0}{1}".format(element, hashlib.md5(".".join([line, record_id or "", key or ""])).hexdigest())

def generate_header_mapping(profile):
    header_map = {}
    
    for mapping in profile.mappings:
        if not mapping.key in header_map:
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
    return render_template("index.html")

@app.route("/run", methods=["POST"])
def step_one():

    # Load files.
    master_file = request.files["master"]
    master_format = trapeza.get_format(master_file.filename, "csv")
    if master_format == "trapeza":
        # Master file has been preprocessed
        processed_master = pickle.load(master_file)
        master = processed_master.source
        profile = processed_master.profile
    else:
        processed_master = None
        master = trapeza.load_source(request.files["master"], master_format)
        profile = trapeza.match.Profile(source = trapeza.load_source(request.files["profile"], trapeza.get_format(request.files["profile"].filename, "csv")))

    incoming = trapeza.load_source(request.files["incoming"], trapeza.get_format(request.files["incoming"].filename, "csv"))
    
    # Determine primary key and set in Master. We do not set the primary key on incoming or the output.
    # If the user feels like matching multiple input lines to the same master records, that's fine;
    # we don't want to throw an exception.
    primary_key = request.form["primary_key"]
    master.set_primary_key(primary_key)
    
    # Bring in the parameters. FIXME: will this crash if a non-integer parameter is submitted?
    cutoff = int(request.form["cutoff"]) or 0
    nresults = int(request.form["nresults"]) or 5
    display_diff = "display_diff" in request.form 
    include_unmatched_records = "include_unmatched_records" in request.form 
    output_only_modified_entries = "output_only_modified_entries" in request.form 
    include_re_new_address_flag = "include_re_new_address_flag" in request.form
    
    # Perform the comparison.
    results = group_results(profile.compare_sources(processed_master or  master, incoming, cutoff), nresults)
    
    # Save the details of the operation
    operation = {   "master": master, "incoming": incoming, "profile": profile, 
                    "primary_key": primary_key, "results": results,
                    "cutoff": cutoff, 
                    "display_diff": display_diff, 
                    "include_unmatched_records": include_unmatched_records, 
                    "output_only_modified_entries": output_only_modified_entries,
                    "include_re_new_address_flag": include_re_new_address_flag }

    outfile = tempfile.NamedTemporaryFile(delete=False)
    session["file"] = outfile.name
    pickle.dump(operation, outfile, pickle.HIGHEST_PROTOCOL)
    outfile.close()

    
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
    with open(session["file"], "rb") as sess_file:
        operation = pickle.load(sess_file)
        
    os.unlink(session["file"])
    
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
                            out_record.values[key] = operation["master"].get_record_with_id(master_id).values[header_map[key]]
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
            
    # If we are outputting unmatched records, we also must collect and output any record which didn't have a match over the cutoff
    # (which won't appear in the matched list)
    
    for record in operation["incoming"].records():
        if record.input_line() not in matched_records:
            out_record = trapeza.Record(record.values)
            out_record.values[primary_key] = u""
            if operation["include_re_new_address_flag"]:
                out_record.values["New Address?"] = "FALSE" 

            output.add_record(record)
            
    io = cStringIO.StringIO()
    
    trapeza.write_source(output, io, "csv")
        
    data = io.getvalue()
    io.close()
    
    response = make_response(data)
    response.headers["Content-Disposition"] = "attachment; filename=output.csv"
    
    return response

if __name__ == "__main__":
    app.debug = True
    app.run()
