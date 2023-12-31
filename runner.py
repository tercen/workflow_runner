import os 
import sys, getopt
import json
import polars as pl


import string, random

import workflow_funcs.workflow_setup as workflow_setup, \
    workflow_funcs.workflow_compare as workflow_compare, \
        workflow_funcs.util as util


from tercen.client.factory import TercenClient

from tercen.model.impl import InitState,  Workflow, Project, GitProjectTask
from tercen.client import context as ctx


def parse_args(argv):
    params = {}
    opts, args = getopt.getopt(argv,"",
                               ["templateRepo=", "gitToken=", "tag=", "branch=",
                                "update_operator=", "quiet",
                                "serviceUri=", "user=", "passw=", "token=",
                                 "tolerance=", "toleranceType=", "taskId=" ]
                                )

    
    

    #docker run --net=host template_tester:0.0.1 --templateRepo=tercen/git_project_test  --gitToken=ddd serviceUri = 'http://127.0.0.1:5400'
    # FIXME DEBUG
    templateRepo = "" #"tercen/git_project_test" #None
   

    params["user"] = 'test'
    params["passw"] = 'test'
    params["token"] = ''
    gitToken = None
    params["verbose"] = True
    params["tag"] = ''
    params["branch"] = 'main'

    params["update_operator"] = False
    
    params["tolerance"] = 0.001
    params["toleranceType"] = "relative"

    params["taskId"] = None

    params["serviceUri"] = "http://127.0.0.1:5400"

    for opt, arg in opts:
        if opt == '-h':
            print('runner.py ARGS')
            sys.exit()

        if opt == '--templateRepo':
            templateRepo = arg

        if opt == '--gitToken':
            gitToken = arg

        if opt == '--serviceUri':
            params["serviceUri"] = arg

        if opt == '--user':
            params["user"] = arg
        
        if opt == '--passw':
            params["passw"] = arg
        
        if opt == '--token':
            params["token"] = arg

        if opt == '--tolerance':
            params["tolerance"] = float(arg)

        if opt == '--toleranceType':
            params["toleranceType"] = arg

        if opt == '--tag':
            params["tag"] = arg

        if opt == '--branch':
            params["branch"] = arg

        if opt == '--quiet':
            params["verbose"] = False

        if opt == '--taskId':
            params["taskId"] = arg

        if opt == params["update_operator"]:
            params["update_operator"] = arg

    
    client = TercenClient(params["serviceUri"])
    client.userService.connect(params["user"], params["passw"])

    params["client"] = client
   
    templateRepo = "https://github.com/" + templateRepo

    params["templateRepo"] = templateRepo
        
    if gitToken == None and "GITHUB_TOKEN" in os.environ:
        gitToken = os.environ["GITHUB_TOKEN"]

    params["gitToken"] = gitToken

    # python3 template_tester.py  --templateRepo=tercen/workflow_lib_repo --templateVersion=latest --templatePath=template_mean_crabs_2.zip --gsRepo=tercen/workflow_lib_repo --gsVersion=latest --gsPath=golden_standard_mean_crabs_2.zip --projectId=2aa4e5e69e49703961f2af4c5e000dd1
    return params


def run_with_params(params):
    try:
        client = params["client"]
        # Create temp project to run tests
        project = Project()
        project.name = 'template_test_' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
        project.acl.owner = params['user']
        project = client.projectService.create(project)
        params["projectId"] = project.id

        # Clone the template project from git
        importTask = GitProjectTask()
        importTask.owner = params['user']
        importTask.state = InitState()

        importTask.addMeta("PROJECT_ID", project.id)
        importTask.addMeta("PROJECT_REV", project.rev)
        importTask.addMeta("GIT_ACTION", "reset/pull")
        importTask.addMeta("GIT_PAT", params["gitToken"])
        importTask.addMeta("GIT_URL", params["templateRepo"])
        
        importTask.addMeta("GIT_BRANCH",params["branch"])
        importTask.addMeta("GIT_MESSAGE", "")
        importTask.addMeta("GIT_TAG", params["tag"])


        importTask = client.taskService.create(importTask)
        client.taskService.runTask(importTask.id)
        importTask = client.taskService.waitDone(importTask.id)
        
        objs = client.persistentService.getDependentObjects(project.id)
        workflowList = util.filter_by_type(objs, Workflow)


        verbose = params["verbose"]
        resultList = []

        allPass = True
        for w in workflowList:
            
            wkfName = w.name

            # FIXME DEBUG
            #if not wkfName.startswith("Complex"):
            # if wkfName != "WizardWkf":
            #     continue
                
            
            nameParts = wkfName.split("_")
            if not (nameParts[-1].startswith("gs") and len(nameParts) > 1):
                wkf = w
                gsWkf = None
                for w2 in workflowList:
                    nameParts = w2.name.split("_")
                    if w2.name == (wkfName + "_" + nameParts[-1]):
                        gsWkf = w2

                        
                        util.msg( "Testing template {} against {}.".format(wkfName, gsWkf.name ), verbose )
                        
                        workflowRun = workflow_setup.setup_workflow(client, wkf, gsWkf=gsWkf, params=params)
                    

                        util.msg("Running all steps", verbose)
                        util.run_workflow(workflowRun, project, client)
                        util.msg("Finished", verbose)

                        # Retrieve the updated, ran workflow
                        workflowRun = client.workflowService.get(workflowRun.id)


                        resultDict = workflow_compare.diff_workflow(client, workflowRun, gsWkf,  params["tolerance"],
                                                params["toleranceType"], verbose)


                        if len(resultDict) > 0:
                            resultList.append({w2.name: resultDict[0]})   
                            allPass = False
                            util.msg("{} and {} comparison FAILED".format(\
                                wkfName, gsWkf.name), verbose)
                        else:
                            util.msg("{} and {} comparison was SUCCESSFUL".format(\
                                wkfName, gsWkf.name), verbose)
        
        gaEnvfile = os.getenv('GITHUB_ENV')
        

        if allPass == False:
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump(resultList, f, ensure_ascii=False, indent=4)     

            if gaEnvfile != None:
                with open(gaEnvfile, "a") as gaFile:
                    gaFile.write(f"SUCCESS=FALSE")
        else:
            if gaEnvfile != None:
                with open(gaEnvfile, "a") as gaFile:
                    gaFile.write(f"SUCCESS=TRUE")

    except Exception as e:
        util.msg("Workflow runner failed with error: ", True)
        util.msg(e.with_traceback(), True)

        with open('test_results.json', 'w', encoding='utf-8') as f:
            json.dump({"Failure":e.with_traceback()}, f, ensure_ascii=False, indent=4)
        
    finally:
        if project != None and client != None:
            client.workflowService.delete(project.id, project.rev)

def run(argv):
    params = parse_args(argv)
    #http://127.0.0.1:5400/test/w/ac44dd4f14f28b0884cf7c9d600027f1/ds/1ba15e7c-6c3e-4521-81f2-d19fa58a57b9
    # params["taskId"] = "someId"
    
    if params["taskId"] != None:
        # TODO Run as operator
        # tercenCtx = ctx.TercenContext(workflowId="ac44dd4f14f28b0884cf7c9d600027f1",\
        #                                stepId="1ba15e7c-6c3e-4521-81f2-d19fa58a57b9")
        tercenCtx = ctx.TercenContext()
        params["client"] = tercenCtx.context.client
  
        df = tercenCtx.rselect()
        
        repoFacName = tercenCtx.rnames


        templateRepo = "https://github.com/" + df[0].to_series()[0]
        params["templateRepo"] = templateRepo

        outDf = pl.DataFrame({".ci": [0, 0, 0],\
                               "git_repo": [df[0].to_series()[0], df[0].to_series()[0], df[0].to_series()[0]],\
                              "workflow": ["Simple", "Simple2", "WizardWkf"],\
                              "status": [1,1,0]})
        outDf = outDf.with_columns(pl.col('.ci').cast(pl.Int32))
        tercenCtx.save(outDf)
    else:
        run_with_params(params)
    




if __name__ == '__main__':
    #absPath = os.path.dirname(os.path.abspath(__file__))
    run(sys.argv[1:])
