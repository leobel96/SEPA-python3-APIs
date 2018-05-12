#!/usr/bin/python3

# global requirements
import threading
import websocket
import requests
import asyncio
import logging
import base64
import time
import json
import sys
import ssl
from os.path import splitext

# local requirements
from .Exceptions import *
from .JSAPObject import *
from .YSAPObject import *

# class ConnectionHandler
class ConnectionHandler:

    """This is the ConnectionHandler class"""

    # constructor
    def __init__(self, File, logLevel = 10):
        
        """Constructor of the ConnectionHandler class"""

        # logger configuration
        self.logger = logging.getLogger("sepaLogger")
        self.logger.setLevel(logLevel)
        self.logger.debug("=== ConnectionHandler::__init__ invoked ===")
        logging.getLogger("urllib3").setLevel(logLevel)
        logging.getLogger("requests").setLevel(logLevel)
            
        # open subscriptions
        self.websockets = {}

        head,tail = splitext (File)
        if tail.upper() == ".JSAP":
            self.configuration = JSAPObject(File)
        elif tail.upper() == ".YSAP":
            self.configuration = YSAPObject(File)
        else:
            raise WrongFileException("Wrong file selected")

    # do HTTP request
    def unsecureRequest(self, reqURI, sparql, isQuery):

        """Method to issue a SPARQL request over HTTP"""

        # debug
        self.logger.debug("=== ConnectionHandler::unsecureRequest invoked ===")

        # perform the request
        headers = {"Accept":"application/json"}
        if isQuery:
            headers["Content-Type"] = "application/sparql-query"
        else:
            headers["Content-Type"] = "application/sparql-update"

        r = requests.post(reqURI, headers = headers, data = sparql.encode("utf-8"))
        r.connection.close()
        return r.status_code, r.text


    # do HTTPS request
    def secureRequest(self, reqURI, sparql, isQuery, registerURI, tokenURI):

        # debug
        self.logger.debug("=== ConnectionHandler::secureRequest invoked ===")
        
        self.register(registerURI)
        self.requestToken(tokenURI)
                
        # perform the request
        self.logger.debug("Performing a secure SPARQL request")
        if isQuery:
            headers = {"Content-Type":"application/sparql-query", 
                       "Accept":"application/json",
                       "Authorization": "Bearer " + self.configuration.jwt}
            r = requests.post(reqURI, headers = headers, data = sparql, verify = False)        
            r.connection.close()
        else:
            headers = {"Content-Type":"application/sparql-update", 
                       "Accept":"application/json",
                       "Authorization": "Bearer " + self.configuration.jwt}
            r = requests.post(reqURI, headers = headers, data = sparql, verify = False)        
            r.connection.close()
            
        # check for errors on token validity
        if r.status_code == 401:
            self.configuration.jwt = None                
            raise TokenExpiredException

        # return
        return r.status_code, r.text

    
    ###################################################
    #
    # registration function
    #
    ###################################################

    def register(self, registerURI):

        # debug print
        self.logger.debug("=== ConnectionHandler::register invoked ===")
        
        # define headers and payload
        headers = {"Content-Type":"application/json", "Accept":"application/json"}
        payload = '{"client_identity":' + self.configuration.client_id + ', "grant_types":["client_credentials"]}'

        # perform the request
        r = requests.post(registerURI, headers = headers, data = payload, verify = False)        
        r.connection.close()
        if r.status_code == 201:

            # parse the response
            jresponse = json.loads(r.text)

            # encode with base64 client_id and client_secret
            cred = base64.b64encode(bytes(jresponse["client_id"] + ":" + jresponse["client_secret"], "utf-8"))
            self.configuration.client_secret = "Basic " + cred.decode("utf-8")
            
            # store data into the jsap file
            self.configuration.storeConfig()

        else:
            raise RegistrationFailedException()


    ###################################################
    #
    # token request
    #
    ###################################################

    # do request token
    def requestToken(self, tokenURI):

        # debug print
        self.logger.debug("=== ConnectionHandler::requestToken invoked ===")
        
        # define headers and payload        
        headers = {"Content-Type":"application/json", 
                   "Accept":"application/json",
                   "Authorization": self.configuration.client_secret}    

        # perform the request
        r = requests.post(tokenURI, headers = headers, verify = False)        
        r.connection.close()
        if r.status_code == 201:
            jresponse = json.loads(r.text)
            self.configuration.jwt = jresponse["access_token"]
        else:
            raise TokenRequestFailedException()


    ###################################################
    #
    # websocket section
    #
    ###################################################

    # do open websocket
    def openUnsecureWebsocket(self, subscribeURI, sparql, alias, handler):                         

        # debug
        self.logger.debug("=== ConnectionHandler::openUnsecureWebsocket invoked ===")

        # initialization
        handler = handler
        subid = None

        # on_message callback
        def on_message(ws, message):

            # debug
            self.logger.debug("=== ConnectionHandler::on_message invoked ===")
            self.logger.debug(message)

            # process message
            jmessage = json.loads(message)
            if "subscribed" in jmessage:

                # get the subid
                nonlocal subid
                subid = jmessage["subscribed"]
                self.logger.debug("SUBID = " + subid)

                # save the subscription id and the thread
                self.websockets[subid] = ws

                # invoke the handler
                added = jmessage["firstResults"]["results"]["bindings"]
                handler.handle(added, [])

            elif "ping" in jmessage:                
                pass # we ignore ping
            else:
                # parsing jmessage
                added = jmessage["results"]["addedresults"]["bindings"]
                removed = jmessage["results"]["removedresults"]["bindings"]
                # debug print
                self.logger.debug("Added bindings: {}".format(added))
                self.logger.debug("Removed bindings: {}".format(removed))

                # invoke the handler
                handler.handle(added,removed)


        # on_error callback
        def on_error(ws, error):

            # debug
            self.logger.debug("=== ConnectionHandler::on_error invoked ===")


        # on_close callback
        def on_close(ws):

            # debug
            self.logger.debug("=== ConnectionHandler::on_close invoked ===")

            # destroy the websocket dictionary
            try:
                del self.websockets[subid]
            except:
                pass


        # on_open callback
        def on_open(ws):           

            # debug
            self.logger.debug("=== ConnectionHandler::on_open invoked ===")

            # composing message
            msg = {}
            msg["subscribe"] = sparql
            msg["alias"] = alias

            # send subscription request
            ws.send(json.dumps(msg))
            self.logger.debug(msg)


        # configuring the websocket
        ws = websocket.WebSocketApp(subscribeURI,
                                    on_message = on_message,
                                    on_error = on_error,
                                    on_close = on_close,
                                    on_open = on_open)                                        

        wst = threading.Thread(target=ws.run_forever)
        wst.daemon = True
        wst.start()

        # return
        while not subid:
            self.logger.debug("Waiting for subscription ID")
            time.sleep(0.1)            
        return subid

    
    # do open websocket
    def openSecureWebsocket(self, subscribeURI, sparql, alias, handler, registerURI, tokenURI):                         

        # debug
        self.logger.debug("=== ConnectionHandler::openSecureWebsocket invoked ===")
        
        # if the client is not yet registered, then register!
        if not self.configuration.client_secret:
            self.register(registerURI)
            
        # if a token is not present, request it!
        if not(self.configuration.jwt):
            self.requestToken(tokenURI)

        # initialization
        handler = handler
        subid = None

        # on_message callback
        def on_message(ws, message):

            # debug
            self.logger.debug("=== ConnectionHandler::on_message invoked ===")
            self.logger.debug(message)

            # process message
            jmessage = json.loads(message)
            if "subscribed" in jmessage:

                # get the subid
                nonlocal subid
                subid = jmessage["subscribed"]
                self.logger.debug("SUBID = " + subid)

                # save the subscription id and the thread
                self.websockets[subid] = ws

                # invoke the handler
                added = jmessage["firstResults"]["results"]["bindings"]
                handler.handle(added, [])

            elif "ping" in jmessage:                
                pass # we ignore ping
            else:
                # parsing jmessage
                added = jmessage["results"]["addedresults"]["bindings"]
                removed = jmessage["results"]["removedresults"]["bindings"]
                # debug print
                self.logger.debug("Added bindings: {}".format(added))
                self.logger.debug("Removed bindings: {}".format(removed))

                # invoke the handler
                handler.handle(added,removed)


        # on_error callback
        def on_error(ws, error):

            # debug
            self.logger.debug("=== ConnectionHandler::on_error invoked ===")


        # on_close callback
        def on_close(ws):

            # debug
            self.logger.debug("=== ConnectionHandler::on_close invoked ===")

            # destroy the websocket dictionary
            try:
                del self.websockets[subid]
            except:
                pass


        # on_open callback
        def on_open(ws):           

            # debug
            self.logger.debug("=== ConnectionHandler::on_open invoked ===")
            
            # composing message
            msg = {}
            msg["subscribe"] = sparql
            msg["alias"] = alias
            msg["authorization"] = self.configuration.jwt

            # send subscription request
            ws.send(json.dumps(msg))
            self.logger.debug(json.dumps(msg))


        # configuring the websocket        
        ws = websocket.WebSocketApp(subscribeURI,
                                    on_message = on_message,
                                    on_error = on_error,
                                    on_close = on_close,
                                    on_open = on_open)                                        

        # starting the websocket thread
        wst = threading.Thread(target=ws.run_forever, kwargs=dict(sslopt={"cert_reqs": ssl.CERT_NONE}))
        wst.daemon = True
        wst.start()

        # return
        while not subid:
            self.logger.debug("Waiting for subscription ID")
            time.sleep(0.1)            
        return subid
    

    def closeWebsocket(self, subid, secure):

        # debug
        self.logger.debug("=== ConnectionHandler::closeWebSocket invoked ===")

        # TODO -- send the unsubscribe message instead of closing in a brutal way
        
        # retrieve the subscription, close it and delete it
        try:
            self.websockets[subid].close()
            del self.websockets[subid]
        except:
            pass
        
