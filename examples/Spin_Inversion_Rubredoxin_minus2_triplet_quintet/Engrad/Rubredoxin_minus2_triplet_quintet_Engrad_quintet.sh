#!/bin/bash
#PBS -N Cluster_code_Engrad_quintet
#PBS -l nodes=1:ppn=12
#PBS -o Cluster_code_Engrad_quintet.pbs.out
#PBS -e Cluster_code_Engrad_quintet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Cluster_code_Engrad_quintet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Cluster_code_Engrad_quintet.inp > Cluster_code_Engrad_quintet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
