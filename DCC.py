#!/usr/bin/env python3

# external modules
from bs4 import BeautifulSoup
import pprint
import re
import requests
import getpass
import json
import os
import sys
from datetime import datetime
import time

# my modules
import config as cf

# References for DCC login
# http://docs.python-requests.org/en/latest/user/quickstart/
# http://docushare.xerox.com/en-us/Help/prog/prog5.htm
# http://docushare.xerox.com/pdf/ds_whitepaper_Security.pdf
# http://customer.docushare.xerox.com/s.nl/ctype.KB/it.I/id.24908/KB.195/.f
# https://docushare.xerox.com/dsdn/dsweb/Get/Document-8931/DocuShare%20HTTP_XML%20Interface%20Protocol%20Specification.pdf


def login(url):
    # See if secrets file exists and if so use credentials from there
    try:
        import secrets
        uname = secrets.pw['DCC']['login']
        pword = secrets.pw['DCC']['password']
    except:
        uname = input("Enter TMT Docushare Username:")
        pword = getpass.getpass()
        
    dname = "DocuShare"
    xml="""<?xml version='1.0' ?><authorization> <username>""" \
    + uname + """</username><password><![CDATA[""" \
    + pword + """]]></password><domain>""" \
    + dname + """</domain></authorization>"""
    headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml"}
    s = requests.Session()
    try:
        r = s.post(url,data=xml,headers=headers)
        r.raise_for_status()
        print('Login status code:', r.status_code)
    except:
        print("Unable to log in")
        print("Status Code:", r.status_code)
#         print('headers:\n', r.headers)
#         print('request headers:\n',r.request.headers)
        exit(0)
        
    c = s.cookies
    # print('Cookies:\n', c)
    return s
    
def change_owner(s, dochandle, userhandle):
    url = cf.dcc_url + "/dsweb/PROPPATCH/" + dochandle
    headers = {"DocuShare-Version":"6.2", "Content-Type":"text/xml", "Accept":"*/*, text/xml", "User-Agent":"DsAxess/4.0", "Accept-Language":"en"}
    xml = '''<?xml version="1.0" ?><propertyupdate><set><prop><entityowner><dsref handle="'''
    xml += userhandle
    xml += '''"/></entityowner></prop></set></propertyupdate>'''
    print(xml)
    r = s.post(url,data=xml,headers=headers)
    print(r.text)
    print(r.headers)
    print("Owner Change Status Code:", r.status_code)


def set_permissions(s,handle,fd):
    # fd follows the permissions dictionary format
    
    url = cf.dcc_url + "/dsweb/PROPPATCH/" + handle
    headers = {"DocuShare-Version":"6.2", "Content-Type":"text/xml", "Accept":"*/*, text/xml", "User-Agent":"DsAxess/4.0", "Accept-Language":"en"}
    xml = '''<?xml version="1.0" ?><propertyupdate><set><prop><acl handle="''' 
    xml += handle
    xml += '''">'''
    for entry in fd:
        read = entry.get('Read','False')
        write = entry.get('Write','False')
        manage = entry.get('Manage','False')
        
        xml += '''<ace><principal><dsref handle="'''
        xml += entry['handle']
        xml += '''"/></principal><grant>'''
        if read == True:
            xml += '''<readlinked/><readobject/><readhistory/>'''
        if write == True:
            xml += '''<writelinked/><writeobject/>'''
        if manage == True:
            xml += '''<manage/>'''
        if 'Collection' in handle:
            xml += '''</grant></ace>'''   
        if 'File' in handle or 'Document' in handle:
            xml += '''</grant><cascade/></ace>'''   
    xml += '''</acl></prop></set></propertyupdate>'''
#     print(xml)
    r = s.post(url,data=xml,headers=headers)
    print("Permission Change Status Code:", r.status_code)
#     print(r.text)
#     print(r.headers)

    
def get_file(s, handle, targetpath, filename):
    # Handle can be a Document-XXXXX, File-XXXXX or a Rendition-XXXXX
    url = cf.dcc_url + "/dsweb/GET/" + handle
    headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml"}
    r = s.post(url,headers=headers) 
#     print(r.headers)
    file = open(targetpath + filename,'wb')
    for chunk in r.iter_content(100000):
        file.write(chunk)
    file.close
    return(r) 
    
def writeProps(r, fname):
    webfile = open(cf.dccfilepath + fname +".html",'wb')
    for chunk in r.iter_content(100000):
        webfile.write(chunk)
    webfile.close
    
def scrapeRes(dom, infSet, depth):
    if infSet == 'DocBasic':
        title = dom.title.text
        filename = dom.document.text
        handle = dom.dsref['handle']
        author = dom.author
        date = dom.getlastmodified.text
        size = int(dom.size.text)
        fd = {'title':title, 'handle':handle, 'filename':filename, 'date':date, 'size':size}
    elif infSet == 'DocDate':
        date = dom.getlastmodified.text
        fd = {'date':date}
    elif infSet == 'Parents':
        colls = dom.find("parents").find_all("dsref")
        locations = []
        for coll in colls:
            locations.append([coll['handle'],coll.displayname.text])
        fd = {'locations':locations}  
    elif infSet == 'DocAll':
        fd = read_dcc_doc_data(dom)
    elif infSet == 'VerAll':
        fd = read_dcc_ver_data(dom)        
    elif infSet == 'Coll':
        if depth == '0':
            fd = read_dcc_coll_data(dom)
        else:
            fd = read_coll_content(dom)
    elif infSet == 'Perms':
        fd = read_dcc_doc_perms(dom)
    return(fd)    
    
def getProps(s, handle, **kwargs):
    # kwargs options:
    #  Depth - Level to get Collection children information ('0', '1' or 'infinity')
    #       '0' returns information on Collection itself
    #       '1' and 'infinity' return information on Collection content
    #  InfoSet = Coll - Collection information (See Depth)
    #  InfoSet = DocAll - All Document information
    #  InfoSet = DocBasic - Document basic information
    #  InfoSet = DocDate - Document last modified date
    #  InfoSet = Parents - Locations of documents or collections
    #  InfoSet = Perms - Document Permissions
    #  InfoSet = VerAll - All Version information
    #  RetDom - Return BeautifulSoup object rather than file data structure
    #  WriteProp = (True|False)
    
    url = cf.dcc_url + "/dsweb/PROPFIND/" + handle
    headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml"}
    infoDic = { 'DocBasic':'<title/><handle/><document/><getlastmodified/><size/>',
                'DocDate': '<getlastmodified/>',
                'Parents': '<parents/>',
                'Perms': '<private/><acl/>',
                'Coll' : '<displayname/><summary/><entityowner/><getcontenttype/><parents/>' }

    infoSet = kwargs.get('InfoSet','DocBasic')
    writeRes = kwargs.get('WriteProp', True)
    retDom = kwargs.get('RetDom',False)
    if infoSet == 'Coll':
        depth = kwargs.get('Depth','0')
        headers['Depth'] = depth 
    
    if infoSet in infoDic:
        xml = """<?xml version="1.0" ?><propfind><prop>""" + infoDic[infoSet] + """</prop></propfind>"""
        if infoSet == 'Coll' and depth == '0':
            r = s.post(url,headers=headers)
        else:
            r = s.post(url,data=xml,headers=headers)
    else:
        r = s.post(url,headers=headers)

    if writeRes:
        writeProps(r, handle + '_' + infoSet)
    dom = BeautifulSoup(r.text)
    if retDom:
        return(dom)
    fd = scrapeRes(dom, infoSet, depth)
    return(fd)

    
def get_collections_in_collection(s, coll, **kwargs):
    c_handles = dcc_get_coll_handles(s, coll, **kwargs)
    colllist = []
    pflag = kwargs.get('Print', True)

    for c in c_handles:
        if 'Collection-' in c:
            if pflag:
                print('Collection: ', c) 
            colllist.append(c)
        else:
            if pflag:
                print('Other: ', c)
    fh = open(cf.dccfilepath + coll + '_colls.txt','w')
    json.dump(colllist, fh)
    fh.close()
    return colllist    
    
def get_files_in_collection(s, coll, **kwargs):
    c_handles = dcc_get_coll_handles(s, coll, **kwargs)
    doclist = []
    try:
        pflag = kwargs.get('Print')
    except:
        pflag = True
    for c in c_handles:
        if 'Document-' in c:
            if pflag:
                print('Document: ', c) 
            doclist.append(c)
        else:
            if pflag:
                print('Other: ', c)
    fh = open(cf.dccfilepath + coll + '_docs.txt','w')
    json.dump(doclist, fh)
    fh.close()
    return doclist


def prop_find(s, target, **kwargs):
    # POST /dscgi/ds.py/PROPFIND/Collection-49 HTTP/1.1
    # Host: docushare.xerox.com
    # Accept: text/xml
    # Content-Type: text/xml
    # Content-Length: xxxx
    #
    # A client may submit a Depth header with a value of "0", "1", or
    # "infinity". The depth input only applies to container objects
    # and will be ignored when given for non-container objects.
    # Clients should not supply a depth for non-container objects. The
    # depth value indicates whether the request is to be applied only
    # to the object identified by <handle> (depth=0), to the object
    # and its immediate children (depth=1), or to the object and all
    # its progeny (depth=infinity). When a depth value is not
    # provided, a value of inifinity will be assumed.
    
    url = cf.dcc_url + "/dsweb/PROPFIND/" + target
    # See if it is a collection
    if 'Collection' in url:
        try:
            depth = kwargs.get('Depth')
            headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml", "Depth":depth} 
        except:    
            headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml", "Depth":"infinity"}
            
        xml = """<?xml version="1.0" ?>
            <propfind>
                <prop>
                    <displayname/><summary/><entityowner/><getcontenttype/><parents/> 
                </prop>
            </propfind>"""     
        r = s.post(url,data=xml,headers=headers)  # Gets limited data
    # Otherwise it's a Version or a Document and we don't use the xml string
    else: 
        headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml"}
        r = s.post(url,headers=headers)     # Gets all data
    return(r)
    
def dom_prop_find(s, target, **kwargs):
    r = prop_find(s, target, **kwargs)
    # Need to add flag to turn on / off writing to file
    webfile = open(cf.dccfilepath + target+".html",'wb')
    for chunk in r.iter_content(100000):
        webfile.write(chunk)
    webfile.close
    dom = BeautifulSoup(r.text)
    return dom

def dcc_move(s, handle, source, dest):
    # Syntax:	MOVE / <handle>
    # HTTP Method:	POST
    # Function:	Move the specified object.
    # Authorization:	ANYONE with Writer access to <handle>.
    # Response Format:	XML   
    # Because DocuShare allows objects to reside in more than one location at
    # the same time, the request header "SOURCE" must be provided to
    # disambiguate the MOVE request. Its value must be the handle of one of
    # the existing parents for <handle>.
    print('Moving ',handle,' from ', source,' to ', dest)
    url = cf.dcc_url + "/dsweb/MOVE/" + handle
    headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml", "SOURCE": source, "DESTINATION": dest}
    r = s.post(url, headers=headers) 
    print(r.text)
    
def dcc_remove_doc_from_coll(s, handle, coll):
    """ Removes the location of the document from the collection
        handle - the document handle
        coll - the parent collection
    """
    # First find other collections where the document exists
    parent_info = getProps(s, handle, InfoSet = 'Parents', WriteProp = True)
    loc = parent_info['locations']
    incoll = False  # Flag to check that the document exists in the collection
    target = None # target should contain a valid target collection
    for l in loc:
        # Find another collection where the document exists that can be the destination
        if coll != l[0]:
            target = l[0]
        if coll == l[0]:
            incoll = True
    if incoll == True and target != None:
        print('Removing ', handle, ' from ', coll)
        dcc_move(s, handle,  coll, target)
    else:
        print('Failed to remove ', handle, ' from ', coll)

def add_docs_2_collections(s, docs, colls):
    # Syntax:	COPY / <handle>
    # HTTP Method:	POST
    # Function:	Copy the specified object.
    # Authorization: ANYONE with Manager access to <handle>.
    # Response Format: XML
    # docs - list of Document-XXXX handles
    # coll - list of Collection-XXXX handles
    # add the list of doc handles to the list of collections
    for c in colls:
        for d in docs:
            print('Adding ', d, ' to ', c)
            url = cf.dcc_url + "/dsweb/COPY/" + d
            headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml", "DESTINATION": c}
            r = s.post(url, headers=headers)
            print(r.text)
            
def download_html(url, cookies, outfile):
    # Writes html from url to outfile
    r = requests.get(url, cookies = cookies)
    webfile = open(cf.dccfilepath + outfile,'wb')
    for chunk in r.iter_content(100000):
        webfile.write(chunk)
    webfile.close
     

def read_dcc_doc_perms(dom):
    fd = {}
    # Permissions
    perms = []
    for p in dom.find_all("ace"):  
        try:     
            pentry = {}
            pentry["handle"] = p.dsref.get('handle')
            pentry["name"] = p.displayname.text
            if p.searchers != None:
                pentry["Search"] = True
            if p.readers != None:
                pentry["Read"] = True
            if p.writers != None:
                pentry["Write"] = True 
            if p.managers != None:
                pentry["Manage"] = True
            perms.append(pentry)  
        except:
            pass
    return(perms)


def read_dcc_doc_data(dom):
    # fill in file data dictionary
    fd = {}
    fd['dccnum'] = get_handle(dom.acl['handle'])
    fd['prefver'] = dom.preferred_version.dsref['handle']
    fd['tmtnum'] = dom.summary.text
    fd['dccname'] = dom.displayname.text
    fd['filename'] = dom.webdav_title.text
    fd['modified'] = dom.getlastmodified.text
    fd['owner-name'] = dom.entityowner.displayname.text
    fd['owner-username'] = dom.entityowner.username.text
    fd['owner-userid'] = dom.entityowner.dsref['handle']
    fd['keywords'] = dom.keywords.text
    fd['date'] = dom.getlastmodified.text
    fd['locations'] = []
    fd['versions'] = []
    colls = dom.find("parents").find_all("dsref")
    for coll in colls:
        fd['locations'].append([coll['handle'],coll.displayname.text])
    vers = dom.find("versions").find_all("version")
    for ver in vers:
        fd['versions'].append([ver.dsref['handle'],ver.comment.text,ver.videntifier.text.zfill(2),ver.username.text])
    # pprint.pprint(fd['versions'])
    # Permissions
    fd["permissions"] = read_dcc_doc_perms(dom)
    return(fd)

def print_perm_info(permlist):
    print("\nPermissions...")
    for perm in sorted(permlist, key = lambda x: x["handle"]):
        print("[",perm["handle"],"]:\t","perms = ",sep="",end="")
        if "Search" in perm.keys():
            print("[Search]", end="")
        if "Read" in perm.keys():
            print("[Read]", end="")
        if "Write" in perm.keys():
           print("[Write]", end="")
        if "Manage" in perm.keys():
            print("[Manage]", end="")
        print(", \"",perm['name'],"\"",sep="")


def print_doc_info(fd):
    print("\n\n*** Document Entry", fd['dccnum'], "***\n")
    print("TMT Document Number: ", fd['tmtnum'])
    print("DCC Document Number/Name: ", fd['dccnum'],", \"",fd['dccname'],"\"",sep="")
    print("DCC Preferred Version: ", fd['prefver'])
    print("File Name: ", "\"",fd['filename'],"\"", sep = "")
    print("Modified Date: ", fd['modified'])
    print("Owner: ", fd['owner-name'],":[",fd['owner-userid'],",",fd['owner-username'],"]", sep="")
    print("Keywords: ", " \"", fd['keywords'], "\"", sep="")
    print("Last Modified: ", fd['date'])
    print_perm_info(fd['permissions'])
    print("\nLocations...")
    for loc in sorted(fd['locations'], key = lambda x: x[0]):
        print(loc[0],", \"",loc[1],"\"", sep="")
    print("\nVersions...")
    for ver in sorted(fd["versions"], key = lambda x: x[2], reverse = True ):
        print("Version:", ver[2], ", [", ver[0], "], [",ver[3], "], \"", ver[1], "\"", sep="")
    print("\n*** End Document Entry", fd['dccnum'], "***\n")
    
def print_doc_basic_info(fd):    
    print("DCC Title: ", fd['title'])
    print("DCC Document Handle/FileName: ", fd['handle'],", \"",fd['filename'],"\"",sep="")
    print("DCC Date: ", fd['date'])
    print("Size: ", fd['size'])

def read_dcc_ver_data(dom):
    # fill in file data dictionary
    fd = {}
    fd['dccver'] = dom.handle.dsref["handle"]
    fd['dccdoc'] = get_handle(dom.find('parents').dsref["handle"])
    fd['dccvernum'] = dom.version_number.text
    fd['dcctitle'] = dom.title.text
    fd['vercomment'] = dom.revision_comments.text
    fd['owner-name'] = dom.entityowner.displayname.text
    fd['owner-username'] = dom.entityowner.username.text
    fd['owner-userid'] = dom.entityowner.dsref['handle']
    fd['date'] = dom.getlastmodified.text
    return(fd)
    
def print_ver_info(fd):
    print("\n\n*** Version Entry", fd['dccver'], "***\n")
    print("Version: ", fd['dccver'])
    print("Version Number: ",fd['dccvernum'])
    print("Version Comment: ", fd['vercomment'])
    print("Version Owner: ", fd['owner-name'],":[",fd['owner-userid'],",",fd['owner-username'],"]", sep="")
    print("Last Modified: ", fd['date'])
    print("\nParent DCC Document Number: ", get_handle(fd['dccdoc']))
    print("Parent Document Title:", "\"", fd['dcctitle'], "\"", sep = "")
    print("\n*** End Version Entry", fd['dccver'], "***\n")
    
def is_preferred_version(vd, fd):
    if fd['prefver'] == vd['dccver']:
        return True
    else:
        return False

def get_handle(url):
    #  Takes url such as 'https://docushare.tmt.org:443/Version-501', returns 'Version-501'
    #  Replaces 'File' with 'Document'
    fileRegex = re.compile(r'File')
    handle = url.split('/')[-1]
    handle = fileRegex.sub('Document', handle)
    return(handle)
    
################################
# Collection Read and Print Defs
################################

def print_dcc_coll_data(fd):
    # used for Depth = '0'
    print("\n\n*** Collection Handle", fd['dccnum'], "***\n")
    print("Title: ", fd['title'])
    print("Summary: ", fd['summary'])
    print("Modified Date: ", fd['modified'])
    print("Owner: ", fd['owner-name'],":[",fd['owner-userid'],",",fd['owner-username'],"]", sep="")
    print_perm_info(fd['permissions'])
    
    print("Parents...")
    for p in fd['parents']:
        print("  [",p[0],"], \"", p[1], "\"", sep = "")
        
    print("Children...")
    for c in fd['children']:
        print("  [",c[0],"], \"", c[1], "\"", sep = "")


def read_dcc_coll_data(dom):
    # Applies to collection data for Depth = '0'
    fd = {}
    fd['title'] = dom.title.text
    fd['dccnum'] = get_handle(dom.acl['handle'])
    fd['summary'] = dom.summary.text
    fd['modified'] = dom.getlastmodified.text
    fd['date'] = dom.getlastmodified.text
    fd['owner-name'] = dom.entityowner.displayname.text
    fd['owner-username'] = dom.entityowner.username.text
    fd['owner-userid'] = dom.entityowner.dsref['handle']

    fd['parents'] = []
    for par in dom.find("parents").find_all("dsref"):
        fd['parents'].append([par['handle'],par.displayname.text])
        
    fd['children'] = []
    for par in dom.find("children").find_all("dsref"):
        fd['children'].append([get_handle(par['handle']),par.displayname.text])

#     try:
#         for par in dom.find_all("parents"):
#             fd['parents'].append([par.dsref['handle'],par.displayname.text])
#     except:
#         fd['parents'] = [fd['name'][1],'No Parent Exists']

    # Permissions
    perms = []
    for p in dom.find_all("ace"):       
        pentry = {}
        pentry["handle"] = p.dsref.get('handle')
        pentry["name"] = p.displayname.text
        if p.searchers != None:
            pentry["Search"] = True
        if p.readers != None:
            pentry["Read"] = True
        if p.writers != None:
            pentry["Write"] = True 
        if p.managers != None:
            pentry["Manage"] = True
        perms.append(pentry)  

    fd["permissions"] = perms
    return(fd)
   
def print_coll_info(clist):
# Used for depth of '1' or 'infinity'
    # pprint.pprint(clist)
    idx = 0
    for c in clist:
        if idx == 0:
            print("\nListing of: [", c['name'][1], "], \"", c['name'][0], "\"", sep = "")
            print("Owner: [", c['owner'][2], "], [", c['owner'][1], "], \"", c['owner'][0], "\"", sep = "")
            print("Parents:")
            for p in c['parents']:
                print("  [",p[0],"], \"", p[1], "\"", sep = "")
            print("\nContents:")
        else: 
            print("  ",'%2d' % idx, ": [", c['name'][1], "], \"", c['name'][0],"\"", sep="")
            print("  Owner: [", c['owner'][2], "], [", c['owner'][1], "], \"", c['owner'][0], "\"", sep = "")
            print("  TMTNum: [", c['tmtnum'], "]", sep = "")
            print("  Parents:")
            for p in c['parents']:
                print("    [",p[0],"], \"", p[1], "\"", sep = "")
            print("")
        idx += 1
    
def read_coll_content(dom):
# Used for depth of '1' or 'infinity'
    clist = []
    for res in dom.find_all("response"):
        fd = {}
        fd['name'] = [res.displayname.text, get_handle(res.href.text)]
        fd['owner'] = [res.entityowner.displayname.text, res.entityowner.username.text,res.entityowner.dsref['handle']]
        fd['tmtnum'] = res.summary.text
        
        fd['parents'] = []
        for par in dom.find("parents").find_all("dsref"):
            fd['parents'].append([par['handle'],par.displayname.text])

#         fd['parents'] = []
#         try:
#             for par in res.find_all("parents"):
#                 fd['parents'].append([par.dsref['handle'],par.displayname.text])
#         except:
#             fd['parents'] = [fd['name'][1],'No Parent Exists']
            
        clist.append(fd)
    return(clist)

def file_read_collection(coll):
    # Reads collection data from a .html file on disk
    htmlfile = '/Users/sroberts/Box Sync/Python/' + coll + '.html'
    fh=open(cf.dccfilepath + htmlfile,'r',encoding='utf-8').read()
    dom = BeautifulSoup(fh)
    clist = read_coll_content(dom)
    return(clist)
    
def dcc_read_collection(s, coll_handle, **kwargs):
    # Reads collection data from the DCC
    dom = dom_prop_find(s, coll_handle, **kwargs)

    clist = read_coll_content(dom)
    return(clist)
    
def dcc_get_coll_handles(s, c_handle, **kwargs):
    clist = dcc_read_collection(s, c_handle, **kwargs)
    h = []
    for c in clist:
        h.append(c['name'][1])
    return h    
        
def check_docs_in_coll(s, dl, cl):
    for c in cl:
        # get the list of documents in the collection
        cdl = get_files_in_collection(s, c)
        for d in dl:
            #  print('testing ', d, ' in ', cdl)
            if not d in cdl:
                print(d, ' not found in ', c)
            else:
                print(d, ' found in ', c)
                
def mkCol(s,parentColl, collName, collDesc):
    # Create a collection, return the handle
    url = cf.dcc_url + "/dsweb/MKCOL/" + parentColl
    
    headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml"}
     
    xml1 = """<?xml version="1.0" ?><propertyupdate><set><prop><displayname><![CDATA["""
    xml2 = """]]></displayname><description>"""
    xml3 = """</description></prop></set></propertyupdate>"""
    xml = xml1 + collName + xml2 + collDesc + xml3   
    
    r = s.post(url,data=xml,headers=headers)  # Gets limited data
    
#     print(xml)
#     print("Status Code:", r.status_code)
#     print('headers:\n', r.headers)
#     print('collection = ',handle)
#     print('r.text:\n',r.text)

    handle = r.headers['docushare-handle']
    return(handle)
                

def traverse(s, coll, dirpath = './', indent = '', **kwargs):
    # traverse follows the collection structure on the DCC and replicates it on the local disk
    pflag = False
    savefiles = kwargs.get('SaveFiles', False)
    exclude = kwargs.get('Exclude', [])        
    maxfilesize = kwargs.get('MaxFileSize', sys.maxsize)
        
    collist = get_collections_in_collection(s, coll, Depth = '1', Print = pflag)
    doclist = get_files_in_collection(s, coll, Depth = '1', Print = pflag)
    cinfo = dcc_read_collection(s, coll, Depth = '0')
    print(indent,'Files in ', coll, ': ', cinfo[0]['name'][0])
    colname = cinfo[0]['name'][0]
    colname = colname.replace('/',' ')
    dirpath = dirpath + colname + '/' 
    if savefiles:
        try:
            os.stat(dirpath)
        except:
            os.mkdir(dirpath) 
    for doc in doclist:
        finfo = getProps(s, handle, InfoSet = 'DocBasic', WriteProp = True)
        print(indent + '\t',doc)
        print(indent + '\t\tTitle: ',finfo['title'])
        print(indent + '\t\tFileName: ',finfo['filename'],' [',finfo['date'],']' ,' [', finfo['size'],' bytes ]')
        filedirpath = dirpath + finfo.get('title').replace('/',' ') + '/'
        filename = finfo.get('filename')
        if savefiles:
            try:
                os.stat(filedirpath)
            except:
                os.mkdir(filedirpath)
        if not os.path.isfile(filedirpath+filename):
            print(indent + "\t\t\tFile doesn't exist")
            if savefiles:
                if finfo['size'] < maxfilesize:
                    print(indent + "\t\t\tGetting file")
                    get_file(s, doc, filedirpath, finfo['filename'])
                else:
                    print(indent + "\t\t\tFile size exceeds MaxFileSize of ", maxfilesize, "bytes")
            else:
                print(indent + "\t\t\tSaveFiles is False, so file will not be downloaded")


        elif (datetime.strptime(finfo['date'],'%a, %d %b %Y %H:%M:%S %Z') - datetime(1970,1,1)).total_seconds() > os.path.getctime(filedirpath+filename):
            print(indent + "\t\t\tFile exists, but is out of date:", time.ctime(os.path.getctime(filedirpath+filename)))
            if savefiles:
                if finfo['size'] < maxfilesize:
                    print(indent + "\t\t\tGetting updated file")
                    get_file(s, doc, filedirpath, finfo['filename'])
                else:
                    print(indent + "\t\t\tFile size exceeds MaxFileSize of ", maxfilesize, "bytes")
            else:
                print(indent + "\t\t\tSaveFiles is False, so file will not be downloaded")
        else:
            print(indent + "\t\t\tFile exists, created:", time.ctime(os.path.getctime(filedirpath+filename)))

    for c in collist:
        if (not c == coll) and (not c in exclude):
            traverse(s, c, dirpath, indent + '\t', **kwargs)
            
def testTraverse():        
#     coll = 'Collection-10725'
#     dirpath = r'/Users/sroberts/Box Sync/TMT DCC Files/M1CS/'
#     exclude = ['Collection-10836', 'Collection-10837']
#     
#     coll = 'Collection-8277'
#     dirpath = r'/Users/sroberts/Box Sync/TMT DCC Files/Configuration Control/'


    
#     exclude = [
#         'Collection-9908', 
#         'Collection-10023', 'Collection-10026', 'Collection-10025', 
#         'Collection-10024', 'Collection-9895', 'Collection-8288', 
#         'Collection-8279', 'Collection-10582', 'Collection-8278', 
#         'Collection-9889', 'Collection-8711', 'Collection-8283',  
#         'Collection-9628', 'Collection-8280', 'Collection-8282', 
#         'Collection-8281'
#         ]

#     dir = os.path.dirname(dirpath)
#     print(dir)
# 
#     try:
#         os.stat(dir)
#     except:
#         os.mkdir(dir)  

    # Login to DCC
    s = login(cf.dcc_url + cf.dcc_login)

#     traverse(s, coll, dirpath, SaveFiles = True, Exclude = exclude, MaxFileSize = 20000000)
    coll = 'Collection-2676'    
    traverse(s, coll, SaveFiles = True, MaxFileSize = 10000)
    

    
    
def testGetColl():
    # Login to DCC
    s = login(cf.dcc_url + cf.dcc_login)
    
    coll = 'Collection-8277'
    clist = get_collections_in_collection(s, coll, Depth = '1')
    print(clist)
    
    handle = 'Document-2688'
    info = getProps(s, handle, InfoSet = 'DocBasic', WriteProp = True)
    print(info)

def testGetProps():
    # Login to DCC
    s = login(cf.dcc_url + cf.dcc_login)
    
    handle = 'Document-2688'


    print('\ngetProps prop_find replacement')
    fd = getProps(s, handle, InfoSet = 'DocAll', WriteProp = True)
    print_doc_info(fd)
    
    print('prop_find test')
    dom = dom_prop_find(s, handle)
    fd1 = read_dcc_doc_data(dom)
    print_doc_info(fd1)
    
    print('FD compare', fd == fd1)
    
    


if __name__ == '__main__':
    print("Running module test code for",__file__)

    testGetProps()
#     testGetBasicInfo()
#     testTraverse()
#     testGetColl()



# def prop_find_coll(s, target):
#     if 'Collection' in target:
#         url = cf.dcc_url + "/dsweb/PROPFIND/" + target
#         headers = {"DocuShare-Version":"5.0", "Content-Type":"text/xml", "Accept":"text/xml", "Depth":"0"}
#         r = s.post(url,headers=headers)     # Gets all data
#         return(r)
#     else:
#         sys.exit('Error: call to prop_find_collection without target collection')

# def dom_prop_find_coll(s, target):
#     r = prop_find_coll(s, target)
#     # Need to add flag to turn on / off writing to file
#     webfile = open(cf.dccfilepath + target+".html",'wb')
#     for chunk in r.iter_content(100000):
#         webfile.write(chunk)
#     webfile.close
#     dom = BeautifulSoup(r.text)
#     return dom

    
